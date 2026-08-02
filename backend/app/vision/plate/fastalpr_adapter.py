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
        try:  # open_image_models renamed the detector class in 0.6.0.
            from open_image_models.detection.core.yolo_v9.inference import (
                YoloV9Detector as YoloV9ObjectDetector,
            )
        except ImportError:  # Older releases still ship the previous name.
            from open_image_models.detection.core.yolo_v9.inference import (
                YoloV9ObjectDetector,
            )

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

    def _read(self, crop: NDArray[np.uint8]) -> PlateReading | None:
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

    def recognize(self, crop: NDArray[np.uint8]) -> PlateReading | None:
        # OCR the raw crop and a contrast-boosted copy, then keep whichever read the model
        # is more confident in. A grimy / under-lit plate loses the fine strokes that tell
        # look-alike digits apart (2 vs 7, 3 vs 9); CLAHE recovers them. Gating on confidence
        # means enhancement can only rescue a dirty read, never corrupt a clean one.
        raw_reading = self._read(crop)
        enhanced_reading = self._read(enhance_for_ocr(crop))
        if (
            raw_reading is not None
            and enhanced_reading is not None
            and raw_reading.raw_text == enhanced_reading.raw_text
            and len(raw_reading.character_confidences)
            == len(enhanced_reading.character_confidences)
        ):
            # Both passes read the SAME plate: keep the more-confident overall read, but expose
            # the WEAKEST per-character confidence across both. This matters for physically
            # occluded plates (a sticker over a digit): CLAHE can visually "clean up" the
            # covered glyph into a wrong-but-confident letter (D->U), so the enhanced pass alone
            # looks certain. Taking the per-position minimum keeps that character's true doubt
            # visible downstream so the plate can be routed to human review.
            winner = (
                enhanced_reading
                if enhanced_reading.confidence > raw_reading.confidence
                else raw_reading
            )
            merged = tuple(
                min(a, b)
                for a, b in zip(
                    raw_reading.character_confidences,
                    enhanced_reading.character_confidences,
                    strict=True,
                )
            )
            return PlateReading(
                raw_text=winner.raw_text,
                confidence=winner.confidence,
                character_confidences=merged,
            )
        if enhanced_reading is not None and (
            raw_reading is None or enhanced_reading.confidence > raw_reading.confidence
        ):
            return enhanced_reading
        return raw_reading


def enhance_for_ocr(crop: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Boost local contrast on a dirty/low-contrast plate crop, for OCR input only.

    Small camera plates lose the fine strokes that separate look-alike digits (2 vs 7,
    3 vs 9) when the plate is grimy or under-lit. Upscaling then CLAHE (contrast-limited
    adaptive histogram equalisation) restores that local contrast and a light unsharp pass
    crisps the edges. Applied to the OCR input only — the stored evidence crop keeps the
    untouched pixels a reviewer needs to trust. Never mutates the input array.
    """

    import cv2

    if crop.size == 0 or crop.ndim != 3:
        return crop
    upscaled = cv2.resize(crop, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    lightness, a_channel, b_channel = cv2.split(cv2.cvtColor(upscaled, cv2.COLOR_BGR2LAB))
    lightness = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lightness)
    balanced = cv2.cvtColor(cv2.merge((lightness, a_channel, b_channel)), cv2.COLOR_LAB2BGR)
    blurred = cv2.GaussianBlur(balanced, (0, 0), 3)
    return cv2.addWeighted(balanced, 1.5, blurred, -0.5, 0)


def crop_bgr(frame: NDArray[np.uint8], bbox: tuple[int, int, int, int]) -> NDArray[np.uint8]:
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = bbox
    x1, x2 = max(0, x1), min(width, x2)
    y1, y2 = max(0, y1), min(height, y2)
    if x2 <= x1 or y2 <= y1:
        raise ValueError("Plate bounding box is outside the frame")
    return frame[y1:y2, x1:x2]


def plate_quality_score(image: NDArray[np.uint8]) -> float:
    """Rank plate crops so best-frame selection picks the most READABLE plate.

    Focus (variance of the Laplacian) is the dominant term — it collapses under motion
    blur, the usual reason a plate that looks fine to the eye crops out smeared. Plate
    resolution rewards the closer, larger pass; contrast and exposure keep mid-tones
    usable. Glare is handled twice: bright pixels are clipped before measuring focus and
    contrast so a specular blob cannot fake sharp edges (the old gradient score ranked a
    glary frame highest), and a saturation penalty pushes blown-out frames down. Returns
    a bounded [0, 1] score.
    """

    import cv2

    if image.size == 0:
        return 0.0
    gray = (cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image).astype(
        np.uint8
    )
    # Brightest channel per pixel catches COLOURED glare (orange tail-light / sodium lamp
    # reflections) that a grayscale blows through — grayscale of orange stays mid-toned.
    brightest = image.max(axis=2) if image.ndim == 3 else gray
    # Flatten glare blobs (white or coloured) so their hard edges cannot fake focus/contrast.
    unglared = gray.copy()
    unglared[brightest >= 240] = 240
    focus = min(1.0, cv2.Laplacian(unglared, cv2.CV_64F).var() / 500.0)
    contrast = float(min(1.0, unglared.astype(np.float32).std() / 64.0))
    resolution = float(min(1.0, (gray.shape[0] * gray.shape[1]) ** 0.5 / 130.0))
    mean_luma = float(gray.astype(np.float32).mean())
    exposure_balance = max(0.0, 1.0 - abs(mean_luma - 135.0) / 135.0)
    # Blown-out fraction in ANY channel — a white plate background (~200-230) stays under
    # 245, but specular glare (white or coloured) crosses it and is penalised.
    saturated_ratio = float(np.mean(brightest >= 245))
    glare = max(0.0, 1.0 - 5.0 * saturated_ratio)
    return float(
        min(
            1.0,
            0.48 * focus
            + 0.18 * resolution
            + 0.12 * contrast
            + 0.07 * exposure_balance
            + 0.15 * glare,
        )
    )
