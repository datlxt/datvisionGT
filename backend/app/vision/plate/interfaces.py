from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

BBox = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class VehicleDetection:
    """A motorcycle observation. Vehicle detection is required for NO_PLATE."""

    bbox: BBox
    confidence: float
    label: str = "motorcycle"


@dataclass(frozen=True, slots=True)
class PlateDetection:
    bbox: BBox
    confidence: float


@dataclass(frozen=True, slots=True)
class PlateReading:
    raw_text: str
    confidence: float
    character_confidences: tuple[float, ...] = ()


class VehicleDetector(Protocol):
    def detect(self, frame: NDArray[np.uint8]) -> list[VehicleDetection]: ...


class PlateDetector(Protocol):
    def detect(self, frame: NDArray[np.uint8]) -> list[PlateDetection]: ...


class PlateRecognizer(Protocol):
    def recognize(self, crop: NDArray[np.uint8]) -> PlateReading | None: ...
