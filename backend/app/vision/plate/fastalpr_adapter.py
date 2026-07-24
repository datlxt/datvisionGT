from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from app.vision.plate.interfaces import PlateDetection, PlateReading


class FastAlprPlateEngine:
    """Offline FastALPR adapter using pinned, job-recorded model artifacts.

    No model is downloaded in a worker. Provisioning is explicit so deployments remain
    reproducible and can run without internet access.
    """

    detector_name = "yolo-v9-t-512-license-plate-end2end"
    ocr_name = "cct-xs-v2-global-model"

    def __init__(
        self,
        detector_path: Path,
        ocr_path: Path,
        ocr_config_path: Path,
        *,
        detection_threshold: float = 0.30,
        providers: tuple[str, ...] = ("CPUExecutionProvider",),
        intra_op_threads: int = 4,
    ) -> None:
        missing = [
            path for path in (detector_path, ocr_path, ocr_config_path) if not path.is_file()
        ]
        if missing:
            raise FileNotFoundError(
                "Missing provisioned ALPR model files: " + ", ".join(map(str, missing))
            )

        import onnxruntime as ort
        from fast_alpr.default_detector import DefaultDetector
        from fast_alpr.default_ocr import DefaultOCR
        from open_image_models.detection.core.yolo_v9.inference import YoloV9ObjectDetector

        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, intra_op_threads)
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        detector = YoloV9ObjectDetector(
            model_path=detector_path,
            class_labels=["License Plate"],
            conf_thresh=detection_threshold,
            providers=list(providers),
            sess_options=options,
        )

        # Reuse FastALPR's battle-tested adapters without triggering its online model hub.
        detector_adapter = object.__new__(DefaultDetector)
        detector_adapter.detector = detector
        self._detector = detector_adapter
        self._ocr = DefaultOCR(
            hub_ocr_model=None,
            device="cpu",
            providers=list(providers),
            sess_options=options,
            model_path=ocr_path,
            config_path=ocr_config_path,
        )

    def detect(self, frame: NDArray[np.uint8]) -> list[PlateDetection]:
        detections = self._detector.predict(frame)
        return [
            PlateDetection(
                bbox=(
                    result.bounding_box.x1,
                    result.bounding_box.y1,
                    result.bounding_box.x2,
                    result.bounding_box.y2,
                ),
                confidence=float(result.confidence),
            )
            for result in detections
        ]

    def recognize(self, crop: NDArray[np.uint8]) -> PlateReading | None:
        result = self._ocr.predict(crop)
        if result is None or not result.text:
            return None
        character_confidences = (
            tuple(float(value) for value in result.confidence)
            if isinstance(result.confidence, list)
            else ()
        )
        confidence = (
            sum(character_confidences) / len(character_confidences)
            if character_confidences
            else float(result.confidence)
        )
        return PlateReading(
            raw_text=result.text,
            confidence=confidence,
            character_confidences=character_confidences,
        )


def crop_bgr(frame: NDArray[np.uint8], bbox: tuple[int, int, int, int]) -> NDArray[np.uint8]:
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = bbox
    x1, x2 = max(0, x1), min(width, x2)
    y1, y2 = max(0, y1), min(height, y2)
    if x2 <= x1 or y2 <= y1:
        raise ValueError("Plate bounding box is outside the frame")
    return frame[y1:y2, x1:x2]


def sharpness_score(image: NDArray[np.uint8]) -> float:
    """Cheap normalized gradient score used for selecting a track's best frame."""

    if image.size == 0:
        return 0.0
    grayscale = (
        image.astype(np.float32).mean(axis=2) if image.ndim == 3 else image.astype(np.float32)
    )
    horizontal = np.abs(np.diff(grayscale, axis=1)).mean() if grayscale.shape[1] > 1 else 0.0
    vertical = np.abs(np.diff(grayscale, axis=0)).mean() if grayscale.shape[0] > 1 else 0.0
    return float(min(1.0, (horizontal + vertical) / 64.0))


def plate_quality_score(image: NDArray[np.uint8]) -> float:
    """Rank plate crops by detail, contrast and usable exposure.

    Sharp saturated glare can score well on gradients alone. Combining sharpness
    with contrast, exposure balance and clipping makes the selected evidence frame
    much more likely to contain every readable character.
    """

    if image.size == 0:
        return 0.0
    grayscale = (
        image.astype(np.float32).mean(axis=2) if image.ndim == 3 else image.astype(np.float32)
    )
    sharpness = sharpness_score(image)
    contrast = float(min(1.0, grayscale.std() / 64.0))
    mean_luma = float(grayscale.mean())
    exposure_balance = max(0.0, 1.0 - abs(mean_luma - 135.0) / 135.0)
    clipped_ratio = float(np.mean((grayscale <= 3.0) | (grayscale >= 252.0)))
    clipping = max(0.0, 1.0 - 2.0 * clipped_ratio)
    return float(
        min(
            1.0,
            0.50 * sharpness
            + 0.25 * contrast
            + 0.15 * exposure_balance
            + 0.10 * clipping,
        )
    )
