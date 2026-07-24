from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


class BoundingBoxError(ValueError):
    pass


@dataclass(frozen=True)
class BoundingBoxXYXY:
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1


def validate_bbox_xyxy(
    value: tuple[float, float, float, float] | list[float],
    *,
    frame_width: int,
    frame_height: int,
    minimum_size: int = 2,
) -> BoundingBoxXYXY:
    if frame_width <= 0 or frame_height <= 0:
        raise BoundingBoxError("Frame dimensions must be positive")
    if len(value) != 4 or any(
        isinstance(coordinate, bool)
        or not isinstance(coordinate, (int, float))
        or not math.isfinite(coordinate)
        for coordinate in value
    ):
        raise BoundingBoxError("Bounding box must contain four finite coordinates")

    x1, y1, x2, y2 = value
    if x1 < 0 or y1 < 0 or x2 > frame_width or y2 > frame_height:
        raise BoundingBoxError("Bounding box is outside the frame")
    if x2 <= x1 or y2 <= y1:
        raise BoundingBoxError("Bounding box has an invalid coordinate order")

    normalized = BoundingBoxXYXY(round(x1), round(y1), round(x2), round(y2))
    if normalized.width < minimum_size or normalized.height < minimum_size:
        raise BoundingBoxError("Bounding box is too small")
    return normalized


def crop_image(image: Any, bbox: BoundingBoxXYXY) -> Any:
    """Crop a PIL-compatible image after explicit bbox validation."""

    if not hasattr(image, "crop"):
        raise TypeError("image must provide a crop method")
    return image.crop((bbox.x1, bbox.y1, bbox.x2, bbox.y2))
