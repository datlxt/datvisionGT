from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

from app.vision.plate.domain import bbox_iou
from app.vision.plate.interfaces import VehicleDetection, VehicleDetector


class FixedCameraMotionDetector:
    """Conservative vehicle-event fallback for a fixed toll-lane camera.

    A generic COCO detector can miss motorcycles seen steeply from above. Motion is not
    used as semantic proof on its own: downstream tracking still requires repeated
    observations before it may emit NO_PLATE.
    """

    model_name = "opencv-mog2-fixed-camera"

    def __init__(
        self,
        *,
        warmup_frames: int = 8,
        min_area_ratio: float = 0.012,
        max_area_ratio: float = 0.65,
    ) -> None:
        import cv2

        self._cv2 = cv2
        self._subtractor = cv2.createBackgroundSubtractorMOG2(
            history=120, varThreshold=24, detectShadows=True
        )
        self._warmup_frames = warmup_frames
        self._min_area_ratio = min_area_ratio
        self._max_area_ratio = max_area_ratio
        self._frame_count = 0

    def detect(self, frame: NDArray[np.uint8]) -> list[VehicleDetection]:
        cv2 = self._cv2
        self._frame_count += 1
        mask = self._subtractor.apply(frame)
        if self._frame_count <= self._warmup_frames:
            return []

        # MOG2 shadows are 127; only retain confidently changed pixels.
        _, mask = cv2.threshold(mask, 240, 255, cv2.THRESH_BINARY)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

        height, width = frame.shape[:2]
        frame_area = height * width
        detections: list[VehicleDetection] = []
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area_ratio = cv2.contourArea(contour) / frame_area
            if not self._min_area_ratio <= area_ratio <= self._max_area_ratio:
                continue
            x, y, box_width, box_height = cv2.boundingRect(contour)
            if box_width < width * 0.08 or box_height < height * 0.10:
                continue
            padding_x = round(box_width * 0.10)
            padding_y = round(box_height * 0.12)
            detections.append(
                VehicleDetection(
                    bbox=(
                        max(0, x - padding_x),
                        max(0, y - padding_y),
                        min(width, x + box_width + padding_x),
                        min(height, y + box_height + padding_y),
                    ),
                    confidence=min(0.65, 0.30 + area_ratio * 4),
                    label="motorcycle_motion_candidate",
                )
            )
        return detections


class CompositeVehicleDetector:
    """Union model and fixed-camera signals while suppressing duplicate boxes."""

    model_name = "yolox-tiny+opencv-mog2"

    def __init__(self, detectors: Sequence[VehicleDetector], *, duplicate_iou: float = 0.45):
        self._detectors = detectors
        self._duplicate_iou = duplicate_iou

    def detect(self, frame: NDArray[np.uint8]) -> list[VehicleDetection]:
        candidates = [item for detector in self._detectors for item in detector.detect(frame)]
        kept: list[VehicleDetection] = []
        for candidate in sorted(candidates, key=lambda item: item.confidence, reverse=True):
            if any(bbox_iou(candidate.bbox, item.bbox) >= self._duplicate_iou for item in kept):
                continue
            kept.append(candidate)
        return kept
