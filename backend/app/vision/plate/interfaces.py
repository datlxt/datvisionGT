from dataclasses import dataclass
from typing import Protocol

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class PlateDetection:
    bbox: tuple[int, int, int, int]
    confidence: float


class PlateDetector(Protocol):
    def detect(self, frame: NDArray[np.uint8]) -> list[PlateDetection]: ...


class PlateRecognizer(Protocol):
    def recognize(self, crop: NDArray[np.uint8]) -> tuple[str, float]: ...

