from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from app.vision.plate.domain import (
    FrameObservation,
    VehicleEventResult,
    consolidate_vehicle_events,
    finalize_vehicle_track,
    is_plausible_vietnamese_plate,
    plate_belongs_to_vehicle,
    plate_key,
)
from app.vision.plate.fastalpr_adapter import crop_bgr, plate_quality_score
from app.vision.plate.interfaces import (
    PlateDetector,
    PlateRecognizer,
    VehicleDetection,
    VehicleDetector,
)
from app.vision.plate.tracker import GreedyVehicleTracker

# The vehicle (YOLOX) and plate (YOLOv9) detectors are INDEPENDENT ONNX sessions, so a frame's two
# detections can run at the SAME time instead of back-to-back. On the profiled lane9 clip they were
# the two biggest costs (vehicle 35% + plate 29% of wall time) and ran serially; overlapping them
# cuts a frame's detection latency from vehicle+plate to max(vehicle, plate). One shared worker is
# reused across all jobs (jobs run one at a time in the RQ worker, so it never over-subscribes) —
# each session still uses its own 2 intra-op threads, so this uses ~4 of 8 cores. Detection is
# otherwise UNCHANGED: same frames, same models, identical boxes → identical GT.
_DETECT_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="frame-detect")


@dataclass(frozen=True, slots=True)
class PipelineFrame:
    frame_number: int
    timestamp_ms: int
    storage_key: str
    bgr: NDArray[np.uint8]


@dataclass(frozen=True, slots=True)
class RearCameraConfig:
    # Normalized region where a vehicle event is allowed.
    roi: tuple[float, float, float, float] = (0.05, 0.05, 0.90, 0.95)
    # Timestamp/camera/legacy OCR overlays must never become model evidence.
    excluded_regions: tuple[tuple[float, float, float, float], ...] = (
        (0.00, 0.85, 0.28, 1.00),
        (0.72, 0.80, 1.00, 1.00),
        (0.75, 0.00, 1.00, 0.15),
    )
    min_no_plate_observations: int = 5
    min_recognized_readings: int = 2
    recognized_threshold: float = 0.75
    orphan_plate_threshold: float = 0.60
    # Lane travel direction ("up"/"down"/"left"/"right") or None. Only flags wrong-direction
    # tracks for review; never drops a real vehicle.
    lane_direction: str | None = None
    # Same plate seen again WITHIN this gap = fragments of one pass (merge); BEYOND it = a separate
    # pass kept as its own row + flagged REPEATED_PLATE. Cars idle longer at a gate and take longer
    # to leave-and-return, so the worker widens this for car lanes.
    cross_plate_merge_gap_ms: int = 90_000


def _center_in_region(
    bbox: tuple[int, int, int, int],
    region: tuple[float, float, float, float],
    width: int,
    height: int,
) -> bool:
    center_x = (bbox[0] + bbox[2]) / 2 / width
    center_y = (bbox[1] + bbox[3]) / 2 / height
    return region[0] <= center_x <= region[2] and region[1] <= center_y <= region[3]


class MotorcyclePlatePipeline:
    """One-row-per-motorcycle video pipeline, including motorcycles with no plate."""

    def __init__(
        self,
        vehicle_detector: VehicleDetector,
        plate_detector: PlateDetector,
        plate_recognizer: PlateRecognizer,
        *,
        config: RearCameraConfig | None = None,
        tracker: GreedyVehicleTracker | None = None,
    ) -> None:
        self.vehicle_detector = vehicle_detector
        self.plate_detector = plate_detector
        self.plate_recognizer = plate_recognizer
        self.config = config or RearCameraConfig()
        self.tracker = tracker or GreedyVehicleTracker()

    def run(
        self,
        frames: Iterable[PipelineFrame],
        on_frame: "Callable[[PipelineFrame, list[FrameObservation], int], None] | None" = None,
        on_event: "Callable[[VehicleEventResult], None] | None" = None,
    ) -> list[VehicleEventResult]:
        results: list[VehicleEventResult] = []

        def _finish(track) -> None:
            event = self._finalize(track)
            results.append(event)
            # A vehicle just finished its pass — hand its (provisional, pre-consolidation) event to
            # the caller so the live list can show it the moment it passes. The observational hooks
            # must NEVER be able to crash the pipeline, so failures are swallowed.
            if on_event is not None:
                try:
                    on_event(event)
                except Exception:
                    pass

        for frame in frames:
            observations = self._observe(frame)
            for track in self.tracker.update(observations, frame.timestamp_ms):
                _finish(track)
            # Optional live hook: hand the raw per-frame detections + running vehicle count to the
            # caller (the worker renders a "live" annotated preview). Purely observational.
            if on_frame is not None:
                try:
                    on_frame(frame, observations, self.tracker._next_id - 1)
                except Exception:
                    pass
        for track in self.tracker.flush():
            _finish(track)
        return consolidate_vehicle_events(
            results, cross_plate_merge_gap_ms=self.config.cross_plate_merge_gap_ms
        )

    def _observe(self, frame: PipelineFrame) -> list[FrameObservation]:
        height, width = frame.bgr.shape[:2]
        # Run the two independent detectors CONCURRENTLY: offload the vehicle detector to the shared
        # worker and run the plate detector on this thread while it works, then join. Both ONNX
        # sessions release the GIL, so this genuinely overlaps. _observe is still called one frame at
        # a time, so the motion detector's order-sensitive background model stays correct.
        vehicle_future = _DETECT_POOL.submit(self.vehicle_detector.detect, frame.bgr)
        raw_plates = self.plate_detector.detect(frame.bgr)
        raw_vehicles = vehicle_future.result()
        vehicles = [
            item
            for item in raw_vehicles
            if _center_in_region(item.bbox, self.config.roi, width, height)
        ]
        plates = [
            item
            for item in raw_plates
            if _center_in_region(item.bbox, self.config.roi, width, height)
            and not any(
                _center_in_region(item.bbox, excluded, width, height)
                for excluded in self.config.excluded_regions
            )
        ]

        candidates: list[tuple[float, int, int]] = []
        for vehicle_index, vehicle in enumerate(vehicles):
            vehicle_area = (vehicle.bbox[2] - vehicle.bbox[0]) * (vehicle.bbox[3] - vehicle.bbox[1])
            for plate_index, plate in enumerate(plates):
                if plate_belongs_to_vehicle(plate.bbox, vehicle.bbox):
                    # Prefer high plate confidence and the tightest containing vehicle.
                    score = plate.confidence + 1 / max(1, vehicle_area)
                    candidates.append((score, vehicle_index, plate_index))

        assignments: dict[int, int] = {}
        used_plates: set[int] = set()
        for _, vehicle_index, plate_index in sorted(candidates, reverse=True):
            if vehicle_index not in assignments and plate_index not in used_plates:
                assignments[vehicle_index] = plate_index
                used_plates.add(plate_index)

        # An unmatched low-confidence rectangle must not create a synthetic vehicle.
        # This previously turned a static floor access panel and rear lamps into records.
        # Keep the recall fallback only when both detector and OCR provide strong evidence.
        orphan_readings = {}
        for plate_index, plate in enumerate(plates):
            if plate_index in used_plates:
                continue
            if plate.confidence < self.config.orphan_plate_threshold:
                continue
            crop = crop_bgr(frame.bgr, plate.bbox)
            reading = self.plate_recognizer.recognize(crop)
            if reading is None or not is_plausible_vietnamese_plate(
                plate_key(reading.raw_text)
            ):
                continue
            plate_width = plate.bbox[2] - plate.bbox[0]
            plate_height = plate.bbox[3] - plate.bbox[1]
            vehicles.append(
                VehicleDetection(
                    bbox=(
                        max(0, plate.bbox[0] - 2 * plate_width),
                        max(0, plate.bbox[1] - 6 * plate_height),
                        min(width, plate.bbox[2] + 2 * plate_width),
                        min(height, plate.bbox[3] + plate_height),
                    ),
                    confidence=plate.confidence,
                    label="plate_anchored_vehicle",
                )
            )
            assignments[len(vehicles) - 1] = plate_index
            orphan_readings[plate_index] = reading
            used_plates.add(plate_index)

        observations: list[FrameObservation] = []
        for vehicle_index, vehicle in enumerate(vehicles):
            plate_index = assignments.get(vehicle_index)
            plate = plates[plate_index] if plate_index is not None else None
            crop = crop_bgr(frame.bgr, plate.bbox) if plate else crop_bgr(frame.bgr, vehicle.bbox)
            reading = (
                orphan_readings.get(plate_index) or self.plate_recognizer.recognize(crop)
                if plate
                else None
            )
            observations.append(
                FrameObservation(
                    frame_number=frame.frame_number,
                    timestamp_ms=frame.timestamp_ms,
                    vehicle_bbox=vehicle.bbox,
                    vehicle_confidence=vehicle.confidence,
                    full_frame_key=frame.storage_key,
                    vehicle_label=vehicle.label,
                    plate_bbox=plate.bbox if plate else None,
                    plate_confidence=plate.confidence if plate else None,
                    reading=reading,
                    quality_score=plate_quality_score(crop),
                )
            )
        return observations

    def _finalize(self, track):  # type annotation omitted to keep runtime adapter lightweight
        return finalize_vehicle_track(
            track,
            min_no_plate_observations=self.config.min_no_plate_observations,
            min_recognized_readings=self.config.min_recognized_readings,
            recognized_threshold=self.config.recognized_threshold,
            lane_direction=self.config.lane_direction,
        )
