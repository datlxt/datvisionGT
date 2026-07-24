import uuid

from app.api.results import EventResult, _merge_persisted_motion_candidates


def event(
    track_code: str,
    *,
    start_ms: int,
    end_ms: int,
    bbox: tuple[int, int, int, int] = (400, 200, 540, 450),
    detections: int = 5,
    plate: str | None = None,
    quality_score: float = 0.5,
) -> EventResult:
    return EventResult(
        track_id=uuid.uuid4(),
        track_code=track_code,
        classification="RECOGNIZED" if plate else "NO_PLATE",
        normalized_plate=plate,
        raw_plate=plate,
        confidence=0.9 if plate else None,
        start_timestamp_ms=start_ms,
        end_timestamp_ms=end_ms,
        best_timestamp_ms=end_ms,
        best_frame_number=end_ms // 40,
        vehicle_bbox=bbox,
        plate_bbox=(430, 350, 520, 420) if plate else None,
        vehicle_confidence=0.6,
        plate_confidence=0.9 if plate else None,
        vehicle_detection_count=detections,
        plate_detection_count=detections if plate else 0,
        quality_score=quality_score,
        quality_flags=[],
        full_frame_url="/frame.jpg",
        vehicle_crop_url="/vehicle.jpg",
        plate_crop_url="/plate.jpg" if plate else None,
    )


def test_low_evidence_no_plate_is_downgraded_for_review() -> None:
    [result] = _merge_persisted_motion_candidates(
        [event("VEHICLE_1", start_ms=1_000, end_ms=2_000, detections=3)]
    )
    assert result.classification == "UNREADABLE"
    assert "INSUFFICIENT_NO_PLATE_EVIDENCE" in result.quality_flags


def test_overlapping_split_no_plate_tracks_are_deduplicated() -> None:
    results = _merge_persisted_motion_candidates(
        [
            event("VEHICLE_1", start_ms=1_000, end_ms=2_000),
            event("VEHICLE_2", start_ms=10_000, end_ms=11_000),
        ]
    )
    assert len(results) == 1
    assert results[0].vehicle_detection_count == 10
    assert "MERGED_DUPLICATE_TRACK" in results[0].quality_flags


def test_persisted_same_plate_is_one_record_and_keeps_best_crop() -> None:
    results = _merge_persisted_motion_candidates(
        [
            event(
                "VEHICLE_1",
                start_ms=1_000,
                end_ms=2_000,
                plate="29X201482",
                quality_score=0.5,
            ),
            event(
                "VEHICLE_2",
                start_ms=30_000,
                end_ms=31_000,
                plate="29X201482",
                quality_score=0.9,
            ),
        ]
    )
    assert len(results) == 1
    assert results[0].quality_score == 0.9
    assert results[0].start_timestamp_ms == 1_000
    assert results[0].end_timestamp_ms == 31_000
    assert "DEDUPLICATED_BY_PLATE" in results[0].quality_flags


def test_adjacent_persisted_ocr_variants_are_one_record() -> None:
    results = _merge_persisted_motion_candidates(
        [
            event(
                "VEHICLE_1",
                start_ms=18_000,
                end_ms=18_760,
                plate="29V201482",
                quality_score=0.4,
            ),
            event(
                "VEHICLE_2",
                start_ms=19_000,
                end_ms=120_000,
                plate="29X201482",
                quality_score=0.9,
            ),
        ]
    )
    assert len(results) == 1
    assert results[0].normalized_plate == "29X201482"
    assert "MERGED_NEAR_OCR_VARIANT" in results[0].quality_flags
