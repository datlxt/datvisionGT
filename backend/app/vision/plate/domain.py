from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from enum import StrEnum

from app.vision.plate.interfaces import BBox, PlateReading


class EventClassification(StrEnum):
    RECOGNIZED = "RECOGNIZED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    UNREADABLE = "UNREADABLE"
    NO_PLATE = "NO_PLATE"


@dataclass(frozen=True, slots=True)
class FrameObservation:
    frame_number: int
    timestamp_ms: int
    vehicle_bbox: BBox
    vehicle_confidence: float
    full_frame_key: str
    vehicle_label: str = "motorcycle"
    plate_bbox: BBox | None = None
    plate_confidence: float | None = None
    plate_crop_key: str | None = None
    reading: PlateReading | None = None
    quality_score: float = 0.0


@dataclass(slots=True)
class VehicleTrack:
    track_code: str
    observations: list[FrameObservation] = field(default_factory=list)

    @property
    def last(self) -> FrameObservation:
        return self.observations[-1]


@dataclass(frozen=True, slots=True)
class VehicleEventResult:
    track_code: str
    classification: EventClassification
    normalized_plate: str | None
    raw_plate: str | None
    confidence: float | None
    start_frame: int
    end_frame: int
    start_timestamp_ms: int
    end_timestamp_ms: int
    best_observation: FrameObservation
    vehicle_detection_count: int
    plate_detection_count: int
    quality_flags: tuple[str, ...]


def normalize_vietnamese_plate(text: str) -> str:
    """Normalize layout punctuation while preserving only OCR-relevant symbols."""

    return re.sub(r"[^A-Z0-9]", "", text.upper())


def is_plausible_vietnamese_plate(value: str) -> bool:
    """Conservative MVP validator for normalized motorcycle/passenger plate text."""

    return bool(re.fullmatch(r"\d{2}[A-Z][A-Z0-9]\d{4,5}", value))


def bbox_iou(left: BBox, right: BBox) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    intersection = (x2 - x1) * (y2 - y1)
    left_area = (left[2] - left[0]) * (left[3] - left[1])
    right_area = (right[2] - right[0]) * (right[3] - right[1])
    return intersection / (left_area + right_area - intersection)


def plate_belongs_to_vehicle(plate: BBox, vehicle: BBox) -> bool:
    """Rear-camera association: plate centre must be inside lower 75% of vehicle box."""

    center_x = (plate[0] + plate[2]) / 2
    center_y = (plate[1] + plate[3]) / 2
    return (
        vehicle[0] <= center_x <= vehicle[2]
        and vehicle[1] + (vehicle[3] - vehicle[1]) * 0.25 <= center_y <= vehicle[3]
    )


def vote_plate(readings: list[tuple[PlateReading, float]]) -> tuple[str, str, float] | None:
    """Confidence/quality weighted track vote, more stable than single-frame OCR."""

    candidates: dict[str, float] = defaultdict(float)
    raw_by_normalized: dict[str, tuple[str, float]] = {}
    for reading, quality in readings:
        normalized = normalize_vietnamese_plate(reading.raw_text)
        if not is_plausible_vietnamese_plate(normalized):
            continue
        weight = max(0.0, reading.confidence) * max(0.05, quality)
        candidates[normalized] += weight
        if normalized not in raw_by_normalized or weight > raw_by_normalized[normalized][1]:
            raw_by_normalized[normalized] = (reading.raw_text, weight)
    if not candidates:
        return None
    winner, winner_weight = max(candidates.items(), key=lambda item: item[1])
    total = sum(candidates.values())
    agreement = winner_weight / total if total else 0.0
    winner_readings = [
        reading.confidence
        for reading, _ in readings
        if normalize_vietnamese_plate(reading.raw_text) == winner
    ]
    mean_confidence = sum(winner_readings) / len(winner_readings)
    confidence = min(1.0, 0.65 * mean_confidence + 0.35 * agreement)
    return winner, raw_by_normalized[winner][0], confidence


def _reading_reliability(reading: PlateReading) -> float:
    """Penalize a frame where one or more characters are obscured by glare."""

    if not reading.character_confidences:
        return reading.confidence
    weakest_character = min(reading.character_confidences)
    return 0.65 * reading.confidence + 0.35 * weakest_character


def finalize_vehicle_track(
    track: VehicleTrack,
    *,
    min_no_plate_observations: int = 5,
    min_recognized_readings: int = 2,
    recognized_threshold: float = 0.75,
) -> VehicleEventResult:
    if not track.observations:
        raise ValueError("Cannot finalize an empty vehicle track")

    plate_observations = [item for item in track.observations if item.plate_bbox is not None]
    readings = [
        (item.reading, item.quality_score)
        for item in plate_observations
        if item.reading is not None
    ]
    vote = vote_plate(readings)
    flags: list[str] = []

    if not plate_observations:
        semantic_vehicle_observations = sum(
            item.vehicle_label == "motorcycle" for item in track.observations
        )
        if semantic_vehicle_observations >= min_no_plate_observations:
            classification = EventClassification.NO_PLATE
        else:
            classification = EventClassification.UNREADABLE
            flags.append(
                "MOTION_ONLY_NO_PLATE_CANDIDATE"
                if semantic_vehicle_observations == 0
                and len(track.observations) >= min_no_plate_observations
                else "INSUFFICIENT_VEHICLE_OBSERVATIONS_FOR_NO_PLATE"
            )
        normalized = raw = None
        confidence = None
        best = max(
            track.observations, key=lambda item: (item.quality_score, item.vehicle_confidence)
        )
    elif vote is None:
        classification = EventClassification.UNREADABLE
        normalized = raw = None
        confidence = None
        best = max(
            plate_observations, key=lambda item: (item.quality_score, item.plate_confidence or 0)
        )
    else:
        normalized, raw, confidence = vote
        winner_reading_count = sum(
            normalize_vietnamese_plate(reading.raw_text) == normalized
            for reading, _ in readings
        )
        classification = (
            EventClassification.RECOGNIZED
            if confidence >= recognized_threshold
            and winner_reading_count >= min_recognized_readings
            else EventClassification.LOW_CONFIDENCE
        )
        if winner_reading_count < min_recognized_readings:
            flags.append("SINGLE_READING_OCR")
        matching_winner_observations = [
            item
            for item in plate_observations
            if item.reading is not None
            and normalize_vietnamese_plate(item.reading.raw_text) == normalized
        ]
        best = max(
            matching_winner_observations,
            key=lambda item: (
                item.quality_score * _reading_reliability(item.reading),
                min(item.reading.character_confidences)
                if item.reading.character_confidences
                else item.reading.confidence,
                item.plate_confidence or 0,
            ),
        )

    first, last = track.observations[0], track.observations[-1]
    return VehicleEventResult(
        track_code=track.track_code,
        classification=classification,
        normalized_plate=normalized,
        raw_plate=raw,
        confidence=confidence,
        start_frame=first.frame_number,
        end_frame=last.frame_number,
        start_timestamp_ms=first.timestamp_ms,
        end_timestamp_ms=last.timestamp_ms,
        best_observation=best,
        vehicle_detection_count=len(track.observations),
        plate_detection_count=len(plate_observations),
        quality_flags=tuple(flags),
    )


def _plate_edit_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def _plate_events_match(
    previous: VehicleEventResult,
    event: VehicleEventResult,
    *,
    same_plate_gap_ms: int,
    near_plate_gap_ms: int,
) -> tuple[bool, bool]:
    if not previous.normalized_plate or not event.normalized_plate:
        return False, False
    gap_ms = event.start_timestamp_ms - previous.end_timestamp_ms
    if previous.normalized_plate == event.normalized_plate:
        return gap_ms <= same_plate_gap_ms, True
    if _plate_edit_distance(previous.normalized_plate, event.normalized_plate) > 2:
        return False, False
    overlaps_in_time = gap_ms <= 0
    same_province_prefix = (
        previous.normalized_plate[:2] == event.normalized_plate[:2]
    )
    # A rear-facing, single-lane camera often creates a short low-confidence track
    # while the motorcycle enters/leaves the ROI. Its best bbox can be far from the
    # main track even though the timestamps are adjacent, so time is the stronger
    # identity signal for the first 500 ms.
    if overlaps_in_time or (same_province_prefix and gap_ms <= 500):
        return True, False
    if gap_ms > near_plate_gap_ms or not same_province_prefix:
        return False, False
    overlaps_in_space = (
        bbox_iou(
            previous.best_observation.vehicle_bbox,
            event.best_observation.vehicle_bbox,
        )
        >= 0.15
    )
    return overlaps_in_space, False


def consolidate_vehicle_events(
    events: list[VehicleEventResult],
    *,
    same_plate_gap_ms: int = 5_000,
    same_no_plate_gap_ms: int = 20_000,
    near_plate_gap_ms: int = 1_500,
) -> list[VehicleEventResult]:
    """Merge split vehicle tracks and publish only evidence-backed events."""

    ordered = sorted(events, key=lambda event: event.start_timestamp_ms)
    merged: list[VehicleEventResult] = []
    for event in ordered:
        match_index = None
        exact_plate_match = False
        for index in range(len(merged) - 1, -1, -1):
            matches, exact = _plate_events_match(
                merged[index],
                event,
                same_plate_gap_ms=same_plate_gap_ms,
                near_plate_gap_ms=near_plate_gap_ms,
            )
            if matches:
                match_index = index
                exact_plate_match = exact
                break
        if match_index is None:
            merged.append(event)
            continue
        previous = merged[match_index]
        best_source = max(
            (previous, event),
            key=lambda item: (
                item.confidence or 0,
                item.plate_detection_count,
                item.best_observation.quality_score,
            ),
        )
        combined_plate_count = previous.plate_detection_count + event.plate_detection_count
        flags = {
            *previous.quality_flags,
            *event.quality_flags,
            "MERGED_DUPLICATE_TRACK",
        }
        classification = best_source.classification
        if (
            exact_plate_match
            and combined_plate_count >= 2
            and (best_source.confidence or 0) >= 0.75
        ):
            classification = EventClassification.RECOGNIZED
        if classification == EventClassification.RECOGNIZED:
            flags.discard("SINGLE_READING_OCR")
        merged[match_index] = replace(
            best_source,
            track_code=previous.track_code,
            classification=classification,
            start_frame=min(previous.start_frame, event.start_frame),
            end_frame=max(previous.end_frame, event.end_frame),
            start_timestamp_ms=min(previous.start_timestamp_ms, event.start_timestamp_ms),
            end_timestamp_ms=max(previous.end_timestamp_ms, event.end_timestamp_ms),
            vehicle_detection_count=(
                previous.vehicle_detection_count + event.vehicle_detection_count
            ),
            plate_detection_count=combined_plate_count,
            quality_flags=tuple(sorted(flags)),
        )

    no_plate_merged: list[VehicleEventResult] = []
    for event in merged:
        if event.classification != EventClassification.NO_PLATE:
            no_plate_merged.append(event)
            continue
        match_index = next(
            (
                index
                for index in range(len(no_plate_merged) - 1, -1, -1)
                if no_plate_merged[index].classification == EventClassification.NO_PLATE
                and event.start_timestamp_ms - no_plate_merged[index].end_timestamp_ms
                <= same_no_plate_gap_ms
                and bbox_iou(
                    event.best_observation.vehicle_bbox,
                    no_plate_merged[index].best_observation.vehicle_bbox,
                )
                >= 0.5
            ),
            None,
        )
        if match_index is None:
            no_plate_merged.append(event)
            continue
        previous = no_plate_merged[match_index]
        best_source = max(
            (previous, event),
            key=lambda item: (
                item.vehicle_detection_count,
                item.best_observation.quality_score,
                item.best_observation.vehicle_confidence,
            ),
        )
        no_plate_merged[match_index] = replace(
            best_source,
            track_code=previous.track_code,
            start_frame=min(previous.start_frame, event.start_frame),
            end_frame=max(previous.end_frame, event.end_frame),
            start_timestamp_ms=min(previous.start_timestamp_ms, event.start_timestamp_ms),
            end_timestamp_ms=max(previous.end_timestamp_ms, event.end_timestamp_ms),
            vehicle_detection_count=(
                previous.vehicle_detection_count + event.vehicle_detection_count
            ),
            quality_flags=tuple(
                sorted(
                    {
                        *previous.quality_flags,
                        *event.quality_flags,
                        "MERGED_DUPLICATE_TRACK",
                    }
                )
            ),
        )
    merged = no_plate_merged

    plated = [event for event in merged if event.plate_detection_count > 0]
    readable = [
        event
        for event in merged
        if event.classification
        in (EventClassification.RECOGNIZED, EventClassification.LOW_CONFIDENCE)
    ]
    consolidated: list[VehicleEventResult] = []
    for event in merged:
        insufficient_vehicle_evidence = (
            "INSUFFICIENT_VEHICLE_OBSERVATIONS_FOR_NO_PLATE" in event.quality_flags
        )
        motion_only = "MOTION_ONLY_NO_PLATE_CANDIDATE" in event.quality_flags
        overlaps_plated_event = any(
            event.start_timestamp_ms <= plate_event.end_timestamp_ms
            and event.end_timestamp_ms >= plate_event.start_timestamp_ms
            for plate_event in plated
        )
        overlaps_readable_event = any(
            event is not readable_event
            and event.start_timestamp_ms <= readable_event.end_timestamp_ms
            and event.end_timestamp_ms >= readable_event.start_timestamp_ms
            for readable_event in readable
        )
        weak_unreadable_plate = (
            event.classification == EventClassification.UNREADABLE
            and event.plate_detection_count > 0
            and (event.best_observation.plate_confidence or 0) < 0.50
        )
        split_unreadable_fragment = (
            event.classification == EventClassification.UNREADABLE
            and event.plate_detection_count == 1
            and overlaps_readable_event
        )
        if (
            event.plate_detection_count == 0
            and overlaps_plated_event
            or insufficient_vehicle_evidence
            or motion_only
            or weak_unreadable_plate
            or split_unreadable_fragment
        ):
            continue
        consolidated.append(event)

    # Product rule for an uploaded video: one normalized plate produces one row.
    # Keep the strongest evidence crop even when the tracker split the same plate
    # into events separated by more than the normal temporal cooldown.
    unique_events: list[VehicleEventResult] = []
    plate_index: dict[str, int] = {}
    for event in sorted(consolidated, key=lambda item: item.start_timestamp_ms):
        if not event.normalized_plate:
            unique_events.append(event)
            continue
        match_index = plate_index.get(event.normalized_plate)
        if match_index is None:
            plate_index[event.normalized_plate] = len(unique_events)
            unique_events.append(event)
            continue
        previous = unique_events[match_index]
        best_source = max(
            (previous, event),
            key=lambda item: (
                item.classification == EventClassification.RECOGNIZED,
                item.confidence or 0,
                item.best_observation.quality_score,
                item.plate_detection_count,
            ),
        )
        combined_plate_count = previous.plate_detection_count + event.plate_detection_count
        flags = {
            *previous.quality_flags,
            *event.quality_flags,
            "DEDUPLICATED_BY_PLATE",
        }
        classification = best_source.classification
        if combined_plate_count >= 2 and (best_source.confidence or 0) >= 0.75:
            classification = EventClassification.RECOGNIZED
            flags.discard("SINGLE_READING_OCR")
        unique_events[match_index] = replace(
            best_source,
            track_code=previous.track_code,
            classification=classification,
            start_frame=min(previous.start_frame, event.start_frame),
            end_frame=max(previous.end_frame, event.end_frame),
            start_timestamp_ms=min(previous.start_timestamp_ms, event.start_timestamp_ms),
            end_timestamp_ms=max(previous.end_timestamp_ms, event.end_timestamp_ms),
            vehicle_detection_count=(
                previous.vehicle_detection_count + event.vehicle_detection_count
            ),
            plate_detection_count=combined_plate_count,
            quality_flags=tuple(sorted(flags)),
        )
    return sorted(unique_events, key=lambda event: event.start_timestamp_ms)
