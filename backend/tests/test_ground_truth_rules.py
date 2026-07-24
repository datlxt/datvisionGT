from __future__ import annotations

from app.services.ground_truth import (
    draft_prediction_text,
    normalize_gt_text,
    verify_blocker,
)


def test_normalize_gt_text_matches_model_normalization() -> None:
    assert normalize_gt_text("29-N1 964.52") == "29N196452"
    assert normalize_gt_text("  89ab24876 ") == "89AB24876"
    assert normalize_gt_text("") is None
    assert normalize_gt_text(None) is None
    assert normalize_gt_text("   ") is None


def test_verify_blocker_requires_gt_and_valid_evidence() -> None:
    assert verify_blocker(normalized_gt_text="29N196452", evidence_status="VALID") is None
    assert verify_blocker(normalized_gt_text=None, evidence_status="VALID") is not None
    assert verify_blocker(normalized_gt_text="", evidence_status="VALID") is not None
    assert verify_blocker(normalized_gt_text="29N196452", evidence_status="PENDING") is not None
    assert verify_blocker(normalized_gt_text="29N196452", evidence_status="INVALID") is not None


def test_draft_prediction_text_drops_plate_for_no_plate_events() -> None:
    assert draft_prediction_text("RECOGNIZED", "29N196452") == "29N196452"
    assert draft_prediction_text("LOW_CONFIDENCE", "29N196452") == "29N196452"
    assert draft_prediction_text("UNREADABLE", None) is None
    assert draft_prediction_text("NO_PLATE", "29N196452") is None
