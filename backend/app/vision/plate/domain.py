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
    """Normalize layout punctuation while preserving only OCR-relevant symbols.

    'Đ' (the electric 'MĐ' series letter) is folded to 'D' FIRST — otherwise the ``[^A-Z0-9]``
    strip would DROP it (Đ is U+0110, not A-Z), turning a human-typed 'MĐ' into a wrong plate that
    no longer matches the OCR's ASCII 'MD'. Folding keeps both spellings equal ('MĐ' == 'MD')."""

    return re.sub(r"[^A-Z0-9]", "", text.upper().replace("Đ", "D"))


# Civilian motorcycle/passenger plate: province(2 digits) + series letter + an OPTIONAL second
# series char + a 4-5 digit block. The optional char covers both the modern 8-9 char plates
# (30E-123.45, 30E1-234.56) AND the legacy 7-char plates on older vehicles (29A-1234, 4 digits).
_CIVILIAN_PLATE_RE = re.compile(r"\d{2}[A-Z][A-Z0-9]?\d{4,5}")
# Military (quân đội): two-letter unit code + 4-5 digit block (e.g. KA1234, BC12345) — RED plates,
# NO province number. Diplomatic / foreign (ngoại giao NG, nước ngoài NN, quốc tế QT, cơ quan CV,
# liên doanh LD): a two-letter marker between two digit groups — the marker sits EITHER right after
# the province (80-NN-167-76 → 80NN16776) OR in the middle after the country + serial code
# (41-291-NG-01 → 41291NG01). Both are read fine by the OCR; only the civilian-only filter discarded
# them.
# Military (Bộ Quốc phòng): a 2-OR-3-letter unit code + a 3-5 digit block, optionally a trailer
# suffix (…RM / …BM). Covers AB-12-34, AB-123, ABS-12-34, ABL-12-34, ABX-12-34, AB-123RM, AB-123BM.
_MILITARY_PLATE_RE = re.compile(r"[A-Z]{2,3}\d{3,5}(?:RM|BM)?")
_DIPLOMATIC_PLATE_RE = re.compile(r"\d{2,5}(?:NG|NN|QT|CV|LD)\d{2,5}")
# Electric two-wheeler (xe điện): a TWO-letter 'MĐ' series (Đ→D so it reads 'MD') + a 5-6 digit
# block, e.g. 29-MĐ2-232.94 → 29MD223294. Regular motorbikes carry a ONE-letter series, so a 2-letter
# 'MD' head is the electric marker. The civilian grammar only allows a 4-5 digit block, so the longer
# electric plates were wrongly rejected; recognise them so a correct read is accepted, kept out of the
# civilian glyph-repair, and flagged for a human (local OCR reads this rare series poorly).
_ELECTRIC_PLATE_RE = re.compile(r"\d{2}MD\d{4,6}")


def is_special_plate(value: str) -> bool:
    """Military / diplomatic / foreign / electric plate whose layout differs from civilian grammar."""

    return bool(
        _MILITARY_PLATE_RE.fullmatch(value)
        or _DIPLOMATIC_PLATE_RE.fullmatch(value)
        or _ELECTRIC_PLATE_RE.fullmatch(value)
    )


def is_plausible_vietnamese_plate(value: str) -> bool:
    """Accept a civilian plate OR a special (military/diplomatic/foreign) plate layout."""

    return bool(_CIVILIAN_PLATE_RE.fullmatch(value)) or is_special_plate(value)


# Position-aware OCR confusion repair. A Vietnamese motorcycle plate is
# `\d{2}[A-Z][A-Z0-9]\d{4,5}`: the province (idx 0-1) and the number block (idx 4+) are
# ALWAYS digits, and the series head (idx 2) is ALWAYS a letter. A blurry frame swaps
# visually identical glyphs across those classes (Z<->2, S<->5, B<->8, G<->6, O/D/Q<->0,
# I/L<->1, A<->4, T<->7). Coercing only the CROSS-CLASS positions is safe: a legal plate
# can never hold a letter where a digit is mandated, so the repair can only move a misread
# toward a legal plate, never corrupt a legal one. Same-class swaps (E<->F, 2<->7) are left
# to the per-character track vote, which a single blurry frame cannot outweigh.
_LETTER_TO_DIGIT = {
    "O": "0",
    "D": "0",
    "Q": "0",
    "I": "1",
    "L": "1",
    "Z": "2",
    "A": "4",
    "S": "5",
    "G": "6",
    "T": "7",
    "B": "8",
}
_DIGIT_TO_LETTER = {
    "0": "D",
    "2": "Z",
    "4": "A",
    "5": "S",
    "6": "G",
    "8": "B",
}


def coerce_to_plate_grammar(value: str) -> str:
    """Repair cross-class OCR glyph swaps using the fixed plate layout classes."""

    if len(value) not in (8, 9):
        return value
    # A special (military/diplomatic) plate does not follow the civilian digit/letter layout —
    # forcing it (e.g. the 'A' in a military 'KA…' → '4') would corrupt a correct read.
    if is_special_plate(value):
        return value
    chars = list(value)
    chars[0] = _LETTER_TO_DIGIT.get(chars[0], chars[0])
    chars[1] = _LETTER_TO_DIGIT.get(chars[1], chars[1])
    chars[2] = _DIGIT_TO_LETTER.get(chars[2], chars[2])
    # idx 3 is [A-Z0-9] (the series can be 1 or 2 glyphs), so its class is ambiguous — leave it.
    for index in range(4, len(chars)):
        chars[index] = _LETTER_TO_DIGIT.get(chars[index], chars[index])
    return "".join(chars)


def plate_key(text: str) -> str:
    """Normalized, grammar-repaired plate string; the identity key for one OCR read."""

    return coerce_to_plate_grammar(normalize_vietnamese_plate(text))


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
    """Per-character, confidence/quality weighted track vote.

    Voting each position independently (instead of the whole string) lets a sharp frame
    outvote a blurry one glyph-by-glyph, so a single smeared character no longer splits
    the tally into two losing candidates. Each read is first grammar-repaired via
    ``plate_key`` so cross-class misreads (Z->2, etc.) already agree before the vote.
    """

    scored: list[tuple[PlateReading, str, float]] = []
    for reading, quality in readings:
        key = plate_key(reading.raw_text)
        if not is_plausible_vietnamese_plate(key):
            continue
        weight = max(0.0, reading.confidence) * max(0.05, quality)
        scored.append((reading, key, weight))
    if not scored:
        return None

    length_weight: dict[int, float] = defaultdict(float)
    for _, key, weight in scored:
        length_weight[len(key)] += weight
    target_length = max(length_weight, key=lambda length: length_weight[length])
    pool = [item for item in scored if len(item[1]) == target_length]

    position_votes: list[dict[str, float]] = [defaultdict(float) for _ in range(target_length)]
    for reading, key, weight in pool:
        aligned = len(reading.character_confidences) == target_length
        for index, character in enumerate(key):
            char_weight = weight * (reading.character_confidences[index] if aligned else 1.0)
            position_votes[index][character] += char_weight

    winner_chars: list[str] = []
    agreements: list[float] = []
    for votes in position_votes:
        character, weight = max(votes.items(), key=lambda item: item[1])
        winner_chars.append(character)
        total = sum(votes.values())
        agreements.append(weight / total if total else 0.0)
    winner = coerce_to_plate_grammar("".join(winner_chars))
    if not is_plausible_vietnamese_plate(winner):
        return None

    exact = [item for item in pool if item[1] == winner]
    source = exact or pool
    raw = max(source, key=lambda item: item[2])[0].raw_text
    mean_confidence = sum(item[0].confidence for item in source) / len(source)
    agreement = sum(agreements) / len(agreements)
    confidence = min(1.0, 0.65 * mean_confidence + 0.35 * agreement)
    return winner, raw, confidence


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
    weak_character_threshold: float = 0.60,
    lane_direction: str | None = None,
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
        # A semantic detection is any real vehicle label (motorcycle, car); motion-only
        # fallbacks carry a "*_motion_candidate" label and must not count as proof.
        semantic_vehicle_observations = sum(
            not item.vehicle_label.endswith("_motion_candidate")
            for item in track.observations
        )
        if semantic_vehicle_observations == 0:
            # Pure motion (MOG2) with no semantic vehicle detection is a review candidate
            # that must not be auto-published as a confirmed vehicle event.
            classification = EventClassification.UNREADABLE
            flags.append("MOTION_ONLY_NO_PLATE_CANDIDATE")
        else:
            # A real vehicle without a plate is still an event (contract rule #5). A short
            # pass is kept and flagged so the reviewer can judge the weaker evidence,
            # instead of being silently dropped from the list.
            classification = EventClassification.NO_PLATE
            if semantic_vehicle_observations < min_no_plate_observations:
                flags.append("LOW_EVIDENCE_NO_PLATE")
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
            plate_key(reading.raw_text) == normalized for reading, _ in readings
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
            and plate_key(item.reading.raw_text) == normalized
        ]
        # High vote confidence can still hide ONE doubtful character — a digit under a sticker
        # or heavy glare that the model consistently misreads (e.g. D read as U). The overall
        # score averages that away, so we surface the single weakest character across the
        # winning frames: if even one position stays uncertain, the plate is flagged for human
        # review instead of being trusted at face value.
        weakest_character = min(
            (
                min(item.reading.character_confidences)
                for item in matching_winner_observations
                if item.reading is not None and item.reading.character_confidences
            ),
            default=1.0,
        )
        if weakest_character < weak_character_threshold:
            flags.append("WEAK_CHARACTER")
        # Show the CLEAREST crop of the whole pass — over ALL readable frames, not only the
        # ones that read the vote winner. When early muddy frames outvote a sharp later one,
        # restricting the crop to winner-matching frames displays a blurry plate while a crisp
        # frame exists; the reviewer needs the sharpest evidence to confirm/correct.
        readable_observations = [
            item for item in plate_observations if item.reading is not None
        ]
        best = max(
            readable_observations or matching_winner_observations,
            key=lambda item: (
                item.quality_score * _reading_reliability(item.reading),
                min(item.reading.character_confidences)
                if item.reading.character_confidences
                else item.reading.confidence,
                item.plate_confidence or 0,
            ),
        )

    # A military / diplomatic plate is read by the same OCR but was not trained on (red plates,
    # different layout), so surface it for a human instead of trusting it silently.
    if normalized and is_special_plate(normalized):
        flags.append("SPECIAL_PLATE")

    # Lane direction check: a genuine pass travels the way the lane runs. A track whose net
    # motion clearly OPPOSES the configured direction (moved more than ~0.75 of its own size the
    # wrong way) is flagged for a human — an adjacent-lane vehicle clipping the edge, a reflection,
    # or a mis-association. Only flags (never drops) so a real vehicle is never lost.
    if lane_direction and len(track.observations) >= 2:
        fv, lv = track.observations[0].vehicle_bbox, track.observations[-1].vehicle_bbox
        dx = (lv[0] + lv[2]) / 2 - (fv[0] + fv[2]) / 2
        dy = (lv[1] + lv[3]) / 2 - (fv[1] + fv[3]) / 2
        vw, vh = max(1.0, fv[2] - fv[0]), max(1.0, fv[3] - fv[1])
        if (
            (lane_direction == "down" and dy < -0.75 * vh)
            or (lane_direction == "up" and dy > 0.75 * vh)
            or (lane_direction == "right" and dx < -0.75 * vw)
            or (lane_direction == "left" and dx > 0.75 * vw)
        ):
            flags.append("WRONG_DIRECTION")

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
    same_no_plate_gap_ms: int = 14_000,
    near_plate_gap_ms: int = 1_500,
    # Max time a plate may be FULLY undetected mid-pass (occluded by a person / barrier / another
    # vehicle) yet still count as the SAME pass. Same-plate reads farther apart than this are kept
    # as SEPARATE passes (a bike that left and re-entered, or a misread collision) AND both are
    # flagged REPEATED_PLATE so a human confirms they are genuinely distinct. 90s tolerates a long
    # stop/occlusion within one pass while staying below the minutes-long gap of a real re-entry.
    # Note: a plate that stays VISIBLE while the vehicle waits is tracked continuously, so a long
    # *stop* alone never triggers this bound.
    cross_plate_merge_gap_ms: int = 90_000,
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
                # The tracker often splits one no-plate motorcycle into several short tracks
                # across its ~5-20s presence. Time proximity is the reliable signal: a moving
                # bike's fragments have low bbox IoU (it travels up the frame), while two
                # different bikes share the same lane spot, so IoU cannot separate them.
                and event.start_timestamp_ms - no_plate_merged[index].end_timestamp_ms
                <= same_no_plate_gap_ms
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
        motion_only = "MOTION_ONLY_NO_PLATE_CANDIDATE" in event.quality_flags
        overlaps_plated_event = any(
            event.start_timestamp_ms <= plate_event.end_timestamp_ms
            and event.end_timestamp_ms >= plate_event.start_timestamp_ms
            for plate_event in plated
        )
        # Overlapping OR within ~1.5s of a readable event: a lone unreadable plate frame
        # that close is the same bike's blurry entry/exit, not a distinct vehicle.
        near_readable_event = any(
            event is not readable_event
            and event.start_timestamp_ms - readable_event.end_timestamp_ms <= 1_500
            and readable_event.start_timestamp_ms - event.end_timestamp_ms <= 1_500
            for readable_event in readable
        )
        weak_unreadable_plate = (
            event.classification == EventClassification.UNREADABLE
            and event.plate_detection_count > 0
            and (event.best_observation.plate_confidence or 0) < 0.50
        )
        split_unreadable_fragment = (
            event.classification == EventClassification.UNREADABLE
            and event.plate_detection_count <= 3
            and event.end_timestamp_ms - event.start_timestamp_ms <= 1_500
            and near_readable_event
        )
        # A vehicle's pass often spawns a short, weakly-read plate fragment with a different
        # string (89C111522 misread as 01E111573; 29C204834 as 29A104114). Edit distance
        # can't bridge those, but a brief low-evidence plate fragment that OVERLAPS in time a
        # stronger plated event is the same bike — it cannot be in two places at once.
        # A weak plate fragment (≤3 readable frames) overlapping a stronger plated event is the
        # same physical bike — it cannot be in two places at once. Drop it when EITHER it is
        # short (≤2s) OR the overlapping event dominates by readable frames (e.g. 1 read vs 61):
        # a real second bike keeps its own sustained track, not a lone misread frame.
        duplicate_plate_fragment = (
            event.plate_detection_count <= 3
            and any(
                other is not event
                and other.plate_detection_count > event.plate_detection_count
                and other.normalized_plate != event.normalized_plate
                and (other.confidence or 0) >= (event.confidence or 0)
                # Same pass even when the fragment sits in a SHORT gap just before/after the strong
                # track (tracker lost then re-acquired the bike a fraction of a second later) — not
                # only when the two windows literally overlap. A blurry pre-pass fragment (36:
                # 120.52–121.28s, 90G…) ends 240ms before the real pass (38: 121.52s, 98D…) — it is
                # the same bike, so allow up to near_plate_gap_ms of separation.
                and event.start_timestamp_ms <= other.end_timestamp_ms + near_plate_gap_ms
                and event.end_timestamp_ms >= other.start_timestamp_ms - near_plate_gap_ms
                and (
                    event.end_timestamp_ms - event.start_timestamp_ms <= 2000
                    or other.plate_detection_count >= event.plate_detection_count * 5
                )
                for other in plated
            )
        )
        # The plate detector fires on the parking card the rider presents at the barrier.
        # Unlike a rear plate (low on the bumper, roughly square), the card sits high in the
        # frame (rider's hand) or has a non-plate aspect. Only unreadable ones are judged so
        # a genuinely dirty real plate (low + plate-shaped) is never dropped.
        card_false_positive = False
        non_plate_shape_read = False
        plate_bbox = event.best_observation.plate_bbox
        if plate_bbox is not None:
            _, vy1, _, vy2 = event.best_observation.vehicle_bbox
            plate_height = plate_bbox[3] - plate_bbox[1]
            vehicle_height = vy2 - vy1
            if plate_height > 0:
                aspect = (plate_bbox[2] - plate_bbox[0]) / plate_height
                # A real 2-row plate is roughly square-to-landscape (aspect ~1.0-2.0). A floor
                # lane-marking is far too wide (aspect ~4), a tail-lamp too tall/square (<1) —
                # anything outside a generous plate band is not a plate shape.
                non_plate_aspect = aspect < 1.0 or aspect > 2.5
                if vehicle_height > 0:
                    vertical_position = (
                        (plate_bbox[1] + plate_bbox[3]) / 2 - vy1
                    ) / vehicle_height
                    card_false_positive = (
                        event.classification == EventClassification.UNREADABLE
                        and (vertical_position < 0.50 or non_plate_aspect)
                    )
                # The detector sometimes fires on a lane-marking / lamp / logo and the OCR turns
                # the noise into a plausible string, so the event is LOW_CONFIDENCE (not
                # UNREADABLE) and slips past the card filter above. A plate read on a SINGLE
                # frame with a non-plate aspect is exactly such a false positive: a real plate is
                # read across many frames as the bike moves, or is at least plate-shaped, so it is
                # never dropped here. (Plate-shaped logos are caught later by the AI cross-check.)
                non_plate_shape_read = (
                    event.classification == EventClassification.LOW_CONFIDENCE
                    and "SINGLE_READING_OCR" in event.quality_flags
                    and non_plate_aspect
                )
        if (
            event.plate_detection_count == 0
            and overlaps_plated_event
            or motion_only
            or weak_unreadable_plate
            or split_unreadable_fragment
            or duplicate_plate_fragment
            or card_false_positive
            or non_plate_shape_read
        ):
            continue
        consolidated.append(event)

    # One normalized plate → one row, but ONLY for the same continuous pass: merge same-plate
    # events that are close in time (a tracker split or brief occlusion). Two appearances of the
    # same string far apart in the video are SEPARATE passes — a returning bike, or two different
    # bikes whose plates were misread to the same text. Merging those would silently drop one real
    # vehicle, turning its stretch of video into a false "missed" gap and losing its evidence, so
    # they are kept as distinct events. `plate_index` tracks the most recent row per plate.
    unique_events: list[VehicleEventResult] = []
    plate_index: dict[str, int] = {}
    for event in sorted(consolidated, key=lambda item: item.start_timestamp_ms):
        if not event.normalized_plate:
            unique_events.append(event)
            continue
        match_index = plate_index.get(event.normalized_plate)
        if match_index is not None and (
            event.start_timestamp_ms - unique_events[match_index].end_timestamp_ms
            > cross_plate_merge_gap_ms
        ):
            # Too far apart to be the same pass — keep as a separate event, but flag BOTH the
            # earlier and this occurrence so a reviewer verifies they are genuinely distinct
            # passes and not one vehicle wrongly split (or two vehicles misread to one plate).
            earlier = unique_events[match_index]
            unique_events[match_index] = replace(
                earlier,
                quality_flags=tuple(sorted({*earlier.quality_flags, "REPEATED_PLATE"})),
            )
            event = replace(
                event,
                quality_flags=tuple(sorted({*event.quality_flags, "REPEATED_PLATE"})),
            )
            match_index = None
        if match_index is None:
            plate_index[event.normalized_plate] = len(unique_events)
            unique_events.append(event)
            continue
        previous = unique_events[match_index]
        best_source = max(
            (previous, event),
            key=lambda item: (
                item.classification == EventClassification.RECOGNIZED,
                # Same normalized plate ⇒ same text, so keep the CLEAREST crop (least
                # glare / sharpest) rather than the highest-confidence but glary one.
                item.best_observation.quality_score,
                item.confidence or 0,
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
        # Merged fragments belong to ONE continuous pass (bounded by cross_plate_merge_gap_ms), so
        # spanning their windows min→max is a tight, correct range — it marks the whole presence
        # for the timeline and video↔list sync without the old cross-video over-merge blowing it up.
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
