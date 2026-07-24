from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from app.vision.plate.interfaces import VehicleDetection


def _nms(boxes: NDArray[np.float32], scores: NDArray[np.float32], threshold: float) -> list[int]:
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes.T
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        index = int(order[0])
        keep.append(index)
        xx1 = np.maximum(x1[index], x1[order[1:]])
        yy1 = np.maximum(y1[index], y1[order[1:]])
        xx2 = np.minimum(x2[index], x2[order[1:]])
        yy2 = np.minimum(y2[index], y2[order[1:]])
        width = np.maximum(0.0, xx2 - xx1 + 1)
        height = np.maximum(0.0, yy2 - yy1 + 1)
        overlap = width * height / (areas[index] + areas[order[1:]] - width * height)
        order = order[np.where(overlap <= threshold)[0] + 1]
    return keep


class YoloXMotorcycleDetector:
    """Apache-2.0 YOLOX-tiny ONNX adapter for motorcycle event detection."""

    model_name = "yolox-tiny"
    motorcycle_class_id = 3  # COCO

    def __init__(
        self,
        model_path: Path,
        *,
        confidence_threshold: float = 0.35,
        nms_threshold: float = 0.45,
        providers: tuple[str, ...] = ("CPUExecutionProvider",),
        intra_op_threads: int = 4,
    ) -> None:
        if not model_path.is_file():
            raise FileNotFoundError(f"Missing provisioned vehicle model: {model_path}")
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, intra_op_threads)
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self._session = ort.InferenceSession(
            str(model_path), providers=list(providers), sess_options=options
        )
        self._input_name = self._session.get_inputs()[0].name
        shape = self._session.get_inputs()[0].shape
        self._input_size = (
            (int(shape[2]), int(shape[3])) if isinstance(shape[2], int) else (416, 416)
        )
        self._confidence_threshold = confidence_threshold
        self._nms_threshold = nms_threshold

    def detect(self, frame: NDArray[np.uint8]) -> list[VehicleDetection]:
        tensor, ratio = self._preprocess(frame)
        output = self._session.run(None, {self._input_name: tensor[None]})[0]
        predictions = self._decode(output[0])
        boxes = predictions[:, :4]
        boxes[:, 0] = predictions[:, 0] - predictions[:, 2] / 2
        boxes[:, 1] = predictions[:, 1] - predictions[:, 3] / 2
        boxes[:, 2] = predictions[:, 0] + predictions[:, 2] / 2
        boxes[:, 3] = predictions[:, 1] + predictions[:, 3] / 2
        boxes /= ratio

        objectness = predictions[:, 4]
        class_scores = predictions[:, 5:]
        scores = objectness * class_scores[:, self.motorcycle_class_id]
        selected = scores >= self._confidence_threshold
        boxes = boxes[selected].astype(np.float32)
        scores = scores[selected].astype(np.float32)
        keep = _nms(boxes, scores, self._nms_threshold)
        height, width = frame.shape[:2]
        results: list[VehicleDetection] = []
        for index in keep:
            x1, y1, x2, y2 = boxes[index]
            bbox = (
                max(0, min(width - 1, round(float(x1)))),
                max(0, min(height - 1, round(float(y1)))),
                max(1, min(width, round(float(x2)))),
                max(1, min(height, round(float(y2)))),
            )
            if bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                results.append(VehicleDetection(bbox=bbox, confidence=float(scores[index])))
        return results

    def _preprocess(self, image: NDArray[np.uint8]) -> tuple[NDArray[np.float32], float]:
        import cv2

        input_height, input_width = self._input_size
        ratio = min(input_height / image.shape[0], input_width / image.shape[1])
        resized = cv2.resize(
            image,
            (int(image.shape[1] * ratio), int(image.shape[0] * ratio)),
            interpolation=cv2.INTER_LINEAR,
        )
        padded = np.full((input_height, input_width, 3), 114, dtype=np.uint8)
        padded[: resized.shape[0], : resized.shape[1]] = resized
        return np.ascontiguousarray(padded.transpose(2, 0, 1), dtype=np.float32), ratio

    def _decode(self, output: NDArray[np.float32]) -> NDArray[np.float32]:
        grids: list[NDArray[np.float32]] = []
        expanded_strides: list[NDArray[np.float32]] = []
        input_height, input_width = self._input_size
        for stride in (8, 16, 32):
            hsize, wsize = input_height // stride, input_width // stride
            yv, xv = np.meshgrid(np.arange(hsize), np.arange(wsize), indexing="ij")
            grid = np.stack((xv, yv), axis=2).reshape(1, -1, 2)
            grids.append(grid.astype(np.float32))
            expanded_strides.append(np.full((*grid.shape[:2], 1), stride, dtype=np.float32))
        grid = np.concatenate(grids, axis=1)[0]
        strides = np.concatenate(expanded_strides, axis=1)[0]
        decoded = output.copy()
        decoded[:, :2] = (decoded[:, :2] + grid) * strides
        decoded[:, 2:4] = np.exp(decoded[:, 2:4]) * strides
        return decoded
