import numpy as np

from app.vision.plate.domain import (
    EventClassification,
    FrameObservation,
    VehicleTrack,
    coerce_to_plate_grammar,
    consolidate_vehicle_events,
    finalize_vehicle_track,
    is_plausible_vietnamese_plate,
    normalize_vietnamese_plate,
    plate_key,
)
from app.vision.plate.fastalpr_adapter import enhance_for_ocr, plate_quality_score
from app.vision.plate.interfaces import PlateDetection, PlateReading, VehicleDetection
from app.vision.plate.pipeline import MotorcyclePlatePipeline, PipelineFrame


def observation(
    index: int,
    *,
    with_plate: bool = False,
    text: str = "",
    vehicle_label: str = "motorcycle",
    vehicle_bbox: tuple[int, int, int, int] = (200, 100, 600, 650),
    plate_confidence: float = 0.9,
    reading_confidence: float = 0.9,
    character_confidences: tuple[float, ...] = (),
    quality_score: float = 0.9,
) -> FrameObservation:
    return FrameObservation(
        frame_number=index,
        timestamp_ms=index * 250,
        vehicle_bbox=vehicle_bbox,
        vehicle_confidence=0.9,
        full_frame_key=f"frame-{index}.jpg",
        vehicle_label=vehicle_label,
        plate_bbox=(350, 450, 470, 520) if with_plate else None,
        plate_confidence=plate_confidence if with_plate else None,
        reading=(
            PlateReading(text, reading_confidence, character_confidences) if text else None
        ),
        quality_score=quality_score,
    )


def test_no_plate_requires_repeated_vehicle_evidence() -> None:
    track = VehicleTrack("VEHICLE_000001", [observation(index) for index in range(5)])
    result = finalize_vehicle_track(track)
    assert result.classification == EventClassification.NO_PLATE
    assert result.normalized_plate is None
    assert result.vehicle_detection_count == 5


def test_car_without_plate_is_a_real_event_not_a_motion_candidate() -> None:
    # A car lane uses COCO class "car"; the semantic check must accept it, so a plateless
    # car is a NO_PLATE event rather than being demoted to a motion-only review candidate.
    track = VehicleTrack(
        "VEHICLE_000001", [observation(index, vehicle_label="car") for index in range(5)]
    )
    result = finalize_vehicle_track(track)
    assert result.classification == EventClassification.NO_PLATE
    assert "MOTION_ONLY_NO_PLATE_CANDIDATE" not in result.quality_flags


def test_single_vehicle_frame_is_a_low_evidence_no_plate_case() -> None:
    # A real (semantic) vehicle without a plate is still an event, even from one frame.
    result = finalize_vehicle_track(VehicleTrack("VEHICLE_000001", [observation(1)]))
    assert result.classification == EventClassification.NO_PLATE
    assert "LOW_EVIDENCE_NO_PLATE" in result.quality_flags


def test_three_semantic_detections_remain_a_no_plate_review_candidate() -> None:
    track = VehicleTrack("VEHICLE_000001", [observation(index) for index in range(3)])
    result = finalize_vehicle_track(track)
    assert result.classification == EventClassification.NO_PLATE
    assert "LOW_EVIDENCE_NO_PLATE" in result.quality_flags


def test_short_no_plate_vehicle_is_kept_not_dropped() -> None:
    # Contract rule #5: a short no-plate pass must still appear in the list.
    event = finalize_vehicle_track(
        VehicleTrack("VEHICLE_000001", [observation(index) for index in range(3)])
    )
    merged = consolidate_vehicle_events([event])
    assert len(merged) == 1
    assert merged[0].classification == EventClassification.NO_PLATE


def test_motion_only_track_is_a_review_candidate_not_confirmed_no_plate() -> None:
    track = VehicleTrack(
        "VEHICLE_000001",
        [observation(index, vehicle_label="motorcycle_motion_candidate") for index in range(5)],
    )
    result = finalize_vehicle_track(track)
    assert result.classification == EventClassification.UNREADABLE
    assert "MOTION_ONLY_NO_PLATE_CANDIDATE" in result.quality_flags


def test_track_vote_normalizes_two_line_vietnamese_plate() -> None:
    track = VehicleTrack(
        "VEHICLE_000001",
        [
            observation(1, with_plate=True, text="29-X2\n014.82"),
            observation(2, with_plate=True, text="29X2 01482"),
            observation(3, with_plate=True, text="29-X2-014.82"),
        ],
    )
    result = finalize_vehicle_track(track)
    assert normalize_vietnamese_plate("29-X2\n014.82") == "29X201482"
    assert result.classification == EventClassification.RECOGNIZED
    assert result.normalized_plate == "29X201482"


def test_ocr_words_and_numeric_noise_are_not_valid_vietnamese_plates() -> None:
    assert is_plausible_vietnamese_plate("29X201482")
    assert not is_plausible_vietnamese_plate("OUT")
    assert not is_plausible_vietnamese_plate("YADEA")
    assert not is_plausible_vietnamese_plate("30144981")
    track = VehicleTrack(
        "VEHICLE_000001", [observation(1, with_plate=True, text="OUT")]
    )
    assert finalize_vehicle_track(track).classification == EventClassification.UNREADABLE


def test_grammar_repair_fixes_cross_class_glyph_swaps() -> None:
    # Province (idx 0-1) and number block (idx 4+) are always digits: a letter there is
    # an OCR swap, so Z->2, B->8, S->5 etc. are safe. The series head (idx 2) is a letter.
    assert coerce_to_plate_grammar("29G12545Z") == "29G125452"  # Z->2 in the number
    assert coerce_to_plate_grammar("B9G125457") == "89G125457"  # B->8 in the province
    assert plate_key("Z9-X2 014.82") == "29X201482"  # normalize + repair together
    # A legal plate must never be corrupted (idx 3 series digit stays a digit).
    assert coerce_to_plate_grammar("29G125457") == "29G125457"


def test_grammar_repair_rescues_a_misread_frame_that_would_be_filtered() -> None:
    # "Z9X201482" (province 2 read as Z) previously failed the plausibility gate and was
    # dropped; grammar repair now recovers the real plate from that single frame.
    track = VehicleTrack(
        "VEHICLE_000001",
        [observation(1, with_plate=True, text="Z9X201482")],
    )
    result = finalize_vehicle_track(track)
    assert result.normalized_plate == "29X201482"


def test_per_character_vote_reconstructs_plate_no_single_frame_read() -> None:
    # No frame read the whole plate correctly, and each wrong frame errs in a different
    # position, so whole-string voting would tie. Per-position voting, weighted by each
    # glyph's confidence, still reconstructs the true "29X201482".
    high = (0.95,) * 9
    blurred_last = (0.95,) * 8 + (0.15,)
    blurred_series = (0.95, 0.95, 0.95, 0.15) + (0.95,) * 5
    track = VehicleTrack(
        "VEHICLE_000001",
        [
            observation(1, with_plate=True, text="29X201482", character_confidences=high),
            observation(2, with_plate=True, text="29X201482", character_confidences=high),
            observation(3, with_plate=True, text="29X201489", character_confidences=blurred_last),
            observation(4, with_plate=True, text="29X901482", character_confidences=blurred_series),
        ],
    )
    result = finalize_vehicle_track(track)
    assert result.normalized_plate == "29X201482"
    assert result.classification == EventClassification.RECOGNIZED


def test_enhance_for_ocr_upscales_without_mutating_input() -> None:
    crop = np.full((40, 60, 3), 120, dtype=np.uint8)
    original = crop.copy()
    enhanced = enhance_for_ocr(crop)
    # 2x upscale for the OCR model, and the source crop (stored as evidence) is untouched.
    assert enhanced.shape[:2] == (80, 120)
    assert np.array_equal(crop, original)


def test_enhance_for_ocr_is_safe_on_degenerate_crops() -> None:
    empty = np.zeros((0, 0, 3), dtype=np.uint8)
    grayscale = np.full((10, 10), 128, dtype=np.uint8)
    assert enhance_for_ocr(empty) is empty
    assert enhance_for_ocr(grayscale) is grayscale


def test_single_ocr_reading_remains_low_confidence() -> None:
    track = VehicleTrack(
        "VEHICLE_000001",
        [observation(1, with_plate=True, text="29X201482")],
    )
    result = finalize_vehicle_track(track)
    assert result.classification == EventClassification.LOW_CONFIDENCE
    assert "SINGLE_READING_OCR" in result.quality_flags


class FakeVehicleDetector:
    def detect(self, _frame):
        return [VehicleDetection((200, 100, 600, 650), 0.95)]


class FakePlateEngine:
    def detect(self, _frame):
        return [
            PlateDetection((350, 450, 470, 520), 0.95),
            PlateDetection((1050, 650, 1250, 710), 0.99),  # legacy OCR overlay
        ]

    def recognize(self, _crop):
        return PlateReading("29-X2 014.82", 0.96)


class EmptyVehicleDetector:
    def detect(self, _frame):
        return []


class WeakFalsePlateEngine:
    def detect(self, _frame):
        return [PlateDetection((600, 160, 690, 230), 0.335)]

    def recognize(self, _crop):
        return None


def test_pipeline_excludes_overlay_and_outputs_one_vehicle_event() -> None:
    pipeline = MotorcyclePlatePipeline(FakeVehicleDetector(), FakePlateEngine(), FakePlateEngine())
    frames = [
        PipelineFrame(
            index, index * 250, f"frame-{index}.jpg", np.zeros((720, 1280, 3), dtype=np.uint8)
        )
        for index in range(3)
    ]
    events = pipeline.run(frames)
    assert len(events) == 1
    assert events[0].classification == EventClassification.RECOGNIZED
    assert events[0].normalized_plate == "29X201482"
    assert events[0].plate_detection_count == 3


def test_unmatched_plate_anchors_a_vehicle_event() -> None:
    pipeline = MotorcyclePlatePipeline(EmptyVehicleDetector(), FakePlateEngine(), FakePlateEngine())
    frames = [
        PipelineFrame(
            index, index * 250, f"frame-{index}.jpg", np.zeros((720, 1280, 3), dtype=np.uint8)
        )
        for index in range(3)
    ]
    events = pipeline.run(frames)
    assert len(events) == 1
    assert events[0].classification == EventClassification.RECOGNIZED
    assert events[0].normalized_plate == "29X201482"


def test_weak_unmatched_rectangle_does_not_create_a_vehicle_event() -> None:
    pipeline = MotorcyclePlatePipeline(
        EmptyVehicleDetector(),
        WeakFalsePlateEngine(),
        WeakFalsePlateEngine(),
    )
    frames = [
        PipelineFrame(
            index,
            index * 250,
            f"frame-{index}.jpg",
            np.zeros((720, 1280, 3), dtype=np.uint8),
        )
        for index in range(3)
    ]
    assert pipeline.run(frames) == []


def test_split_tracks_with_same_plate_are_merged_within_cooldown() -> None:
    first = finalize_vehicle_track(
        VehicleTrack(
            "VEHICLE_000001", [observation(1, with_plate=True, text="29X201482")]
        )
    )
    second = finalize_vehicle_track(
        VehicleTrack(
            "VEHICLE_000002", [observation(10, with_plate=True, text="29X201482")]
        )
    )
    merged = consolidate_vehicle_events([first, second])
    assert len(merged) == 1
    assert merged[0].classification == EventClassification.RECOGNIZED
    assert merged[0].plate_detection_count == 2
    assert "MERGED_DUPLICATE_TRACK" in merged[0].quality_flags


def test_motion_only_fragments_are_not_published_as_vehicle_events() -> None:
    first = finalize_vehicle_track(
        VehicleTrack(
            "VEHICLE_000001",
            [
                observation(index, vehicle_label="motorcycle_motion_candidate")
                for index in range(5)
            ],
        )
    )
    second = finalize_vehicle_track(
        VehicleTrack(
            "VEHICLE_000002",
            [
                observation(index, vehicle_label="motorcycle_motion_candidate")
                for index in range(5, 10)
            ],
        )
    )
    merged = consolidate_vehicle_events([first, second])
    assert merged == []


def test_near_ocr_variants_on_same_vehicle_are_merged() -> None:
    first = finalize_vehicle_track(
        VehicleTrack(
            "VEHICLE_000001",
            [observation(1, with_plate=True, text="29V201482")],
        )
    )
    second = finalize_vehicle_track(
        VehicleTrack(
            "VEHICLE_000002",
            [
                observation(5, with_plate=True, text="29X201482"),
                observation(6, with_plate=True, text="29X201482"),
            ],
        )
    )
    merged = consolidate_vehicle_events([first, second])
    assert len(merged) == 1
    assert merged[0].normalized_plate == "29X201482"
    assert merged[0].classification == EventClassification.RECOGNIZED


def test_adjacent_near_ocr_tracks_merge_even_when_best_boxes_moved() -> None:
    first = finalize_vehicle_track(
        VehicleTrack(
            "VEHICLE_000001",
            [
                observation(
                    1,
                    with_plate=True,
                    text="89W111188",
                    vehicle_bbox=(450, 500, 680, 720),
                )
            ],
        )
    )
    second = finalize_vehicle_track(
        VehicleTrack(
            "VEHICLE_000002",
            [
                observation(
                    2,
                    with_plate=True,
                    text="89W111518",
                    vehicle_bbox=(300, 0, 730, 250),
                ),
                observation(
                    3,
                    with_plate=True,
                    text="89W111518",
                    vehicle_bbox=(300, 0, 730, 250),
                ),
            ],
        )
    )
    merged = consolidate_vehicle_events([first, second])
    assert len(merged) == 1
    assert merged[0].normalized_plate == "89W111518"


def test_overlapping_near_ocr_tracks_merge_despite_prefix_error() -> None:
    first = finalize_vehicle_track(
        VehicleTrack(
            "VEHICLE_000001",
            [
                observation(index, with_plate=True, text="29L555282")
                for index in range(1, 4)
            ],
        )
    )
    second = finalize_vehicle_track(
        VehicleTrack(
            "VEHICLE_000002",
            [observation(3, with_plate=True, text="20L555282")],
        )
    )
    merged = consolidate_vehicle_events([first, second])
    assert len(merged) == 1
    assert merged[0].normalized_plate == "29L555282"


def test_weak_unreadable_plate_candidate_is_not_published() -> None:
    result = finalize_vehicle_track(
        VehicleTrack(
            "VEHICLE_000001",
            [
                observation(
                    index,
                    with_plate=True,
                    plate_confidence=0.459,
                )
                for index in range(1, 4)
            ],
        )
    )
    assert result.classification == EventClassification.UNREADABLE
    assert consolidate_vehicle_events([result]) == []


def test_single_unreadable_fragment_overlapping_readable_track_is_removed() -> None:
    fragment = finalize_vehicle_track(
        VehicleTrack(
            "VEHICLE_000001",
            [observation(2, with_plate=True, plate_confidence=0.7)],
        )
    )
    readable = finalize_vehicle_track(
        VehicleTrack(
            "VEHICLE_000002",
            [
                observation(index, with_plate=True, text="29S700863")
                for index in range(1, 4)
            ],
        )
    )
    merged = consolidate_vehicle_events([fragment, readable])
    assert len(merged) == 1
    assert merged[0].normalized_plate == "29S700863"


def test_no_plate_fragments_within_presence_window_are_merged() -> None:
    # One motorcycle split by the tracker into short fragments a few seconds apart
    # (obs*250ms: 0-1000ms then 5000-6000ms, gap 4s) → one event, not duplicates.
    first = finalize_vehicle_track(
        VehicleTrack("VEHICLE_000001", [observation(index) for index in range(5)])
    )
    second = finalize_vehicle_track(
        VehicleTrack("VEHICLE_000002", [observation(index) for index in range(20, 25)])
    )
    merged = consolidate_vehicle_events([first, second])
    assert len(merged) == 1
    assert merged[0].classification == EventClassification.NO_PLATE
    assert merged[0].vehicle_detection_count == 10
    assert "MERGED_DUPLICATE_TRACK" in merged[0].quality_flags


def test_distinct_sequential_no_plate_vehicles_are_kept_separate() -> None:
    # Two different no-plate bikes with a large gap (lane cleared: 0-1000ms then 20-21s)
    # are two cases and must not be collapsed into one.
    first = finalize_vehicle_track(
        VehicleTrack("VEHICLE_000001", [observation(index) for index in range(5)])
    )
    second = finalize_vehicle_track(
        VehicleTrack("VEHICLE_000002", [observation(index) for index in range(80, 85)])
    )
    merged = consolidate_vehicle_events([first, second])
    assert len(merged) == 2


def test_best_crop_is_the_clearest_frame_even_if_it_read_differently() -> None:
    # For review, the crop must be the SHARPEST frame of the pass so the human can confirm
    # or correct — even if the model read that frame differently from the vote winner. The
    # reading still comes from the multi-frame vote; only the displayed evidence follows
    # clarity (fixes: early muddy frame cropped while a later crisp frame is ignored).
    track = VehicleTrack(
        "VEHICLE_000001",
        [
            observation(1, with_plate=True, text="29X201482", quality_score=0.75),
            observation(2, with_plate=True, text="29X201482", quality_score=0.70),
            observation(
                3,
                with_plate=True,
                text="29V201482",
                reading_confidence=0.99,
                quality_score=1.0,
            ),
        ],
    )
    result = finalize_vehicle_track(track)
    assert result.normalized_plate == "29X201482"  # reading = multi-frame vote
    assert result.best_observation.frame_number == 3  # crop = clearest frame


def test_best_frame_penalizes_one_glared_character() -> None:
    track = VehicleTrack(
        "VEHICLE_000001",
        [
            observation(
                1,
                with_plate=True,
                text="29X201482",
                reading_confidence=0.95,
                character_confidences=(0.99, 0.99, 0.99, 0.99, 0.20, 0.99, 0.99, 0.99, 0.99),
                quality_score=0.90,
            ),
            observation(
                2,
                with_plate=True,
                text="29X201482",
                reading_confidence=0.92,
                character_confidences=(0.90,) * 9,
                quality_score=0.90,
            ),
        ],
    )
    assert finalize_vehicle_track(track).best_observation.frame_number == 2


def test_plate_quality_rejects_fully_clipped_glare() -> None:
    glared = np.full((80, 140, 3), 255, dtype=np.uint8)
    readable = np.zeros((80, 140, 3), dtype=np.uint8)
    readable[:, ::8] = 220
    assert plate_quality_score(readable) > plate_quality_score(glared)


def test_plate_quality_prefers_focus_over_motion_blur() -> None:
    import cv2

    sharp = np.zeros((80, 140, 3), dtype=np.uint8)
    sharp[:, ::6] = 210  # crisp vertical strokes like plate characters
    blurred = cv2.GaussianBlur(sharp, (0, 0), 2.5)
    assert plate_quality_score(sharp) > plate_quality_score(blurred)


def test_plate_quality_prefers_the_larger_closer_plate() -> None:
    import cv2

    large = np.zeros((90, 150, 3), dtype=np.uint8)
    large[:, ::6] = 210
    small = cv2.resize(large, None, fx=0.4, fy=0.4, interpolation=cv2.INTER_AREA)
    assert plate_quality_score(large) > plate_quality_score(small)


def test_plate_quality_does_not_reward_specular_glare() -> None:
    import cv2

    readable = np.zeros((90, 150, 3), dtype=np.uint8)
    readable[:, ::6] = 210
    glary = readable.copy()
    cv2.ellipse(glary, (75, 45), (45, 26), 0, 0, 360, (255, 255, 255), -1)
    # The old gradient score ranked a glary blob highest; clipped focus must not.
    assert plate_quality_score(readable) > plate_quality_score(glary)


def test_plate_quality_penalizes_coloured_glare() -> None:
    import cv2

    # Orange tail-light glare stays mid-toned in grayscale but blows out the red channel;
    # the max-channel saturation term must still catch it and score it below a clean crop.
    readable = np.zeros((90, 150, 3), dtype=np.uint8)
    readable[:, ::6] = 210
    glary = readable.copy()
    cv2.ellipse(glary, (75, 45), (45, 26), 0, 0, 360, (0, 120, 255), -1)  # BGR orange
    assert plate_quality_score(readable) > plate_quality_score(glary)


def test_same_plate_is_one_record_across_the_whole_video() -> None:
    early = finalize_vehicle_track(
        VehicleTrack(
            "VEHICLE_000001",
            [
                observation(
                    index,
                    with_plate=True,
                    text="29X201482",
                    quality_score=0.55,
                )
                for index in range(1, 3)
            ],
        )
    )
    later = finalize_vehicle_track(
        VehicleTrack(
            "VEHICLE_000002",
            [
                observation(
                    index,
                    with_plate=True,
                    text="29X201482",
                    quality_score=0.95,
                )
                for index in range(100, 102)
            ],
        )
    )
    [result] = consolidate_vehicle_events([early, later])
    assert result.track_code == "VEHICLE_000001"
    assert result.best_observation.frame_number == 100
    assert result.plate_detection_count == 4
    assert "DEDUPLICATED_BY_PLATE" in result.quality_flags
