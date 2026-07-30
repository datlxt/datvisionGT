from app.vision.media.condense import Segment, plan_segments, total_kept_ms


def test_no_activity_yields_no_segments() -> None:
    assert plan_segments([], duration_ms=60_000) == []


def test_isolated_blip_is_dropped_as_noise() -> None:
    # A single stray detection with no sustained activity is not a vehicle pass.
    assert plan_segments([10_000], duration_ms=60_000, min_active_ms=500) == []


def test_two_distant_passes_become_two_padded_segments() -> None:
    active = [5_000, 5_250, 5_500, 40_000, 40_250, 40_500]  # two bikes ~35s apart
    segments = plan_segments(
        active, duration_ms=60_000, pad_ms=750, merge_gap_ms=2_000, min_active_ms=500
    )
    assert segments == [Segment(4_250, 6_250), Segment(39_250, 41_250)]


def test_nearby_activity_is_joined_into_one_pass() -> None:
    # A frame missed mid-pass (1.5s gap < merge_gap) must not split the vehicle in two.
    active = [10_000, 11_500, 13_000]
    [segment] = plan_segments(active, duration_ms=60_000, merge_gap_ms=2_000)
    assert segment == Segment(9_250, 13_750)


def test_padding_is_clamped_to_video_bounds() -> None:
    active = [100, 400, 700]  # span 600ms >= min_active, padding runs past both ends
    [segment] = plan_segments(active, duration_ms=1_000, pad_ms=750)
    assert segment == Segment(0, 1_000)


def test_segments_overlapping_after_padding_are_merged() -> None:
    # Two runs 1s apart: padding (0.75s each) makes them overlap -> one segment.
    active = [5_000, 5_500, 6_500, 7_000]
    [segment] = plan_segments(
        active, duration_ms=60_000, pad_ms=750, merge_gap_ms=1_200, min_active_ms=400
    )
    assert segment == Segment(4_250, 7_750)
    assert total_kept_ms([segment]) == 3_500
