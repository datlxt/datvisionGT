from app import models  # noqa: F401
from app.db.base import Base

EXPECTED_TABLES = {
    "artifacts",
    "camera_configs",
    "detections",
    "exports",
    "ground_truth_records",
    "job_events",
    "job_models",
    "model_versions",
    "processing_jobs",
    "recognition_results",
    "review_actions",
    "tracks",
    "users",
}


def test_plate_mvp_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_processing_job_captures_reproducibility_fields() -> None:
    columns = set(Base.metadata.tables["processing_jobs"].columns.keys())
    assert {
        "source_hash",
        "camera_config_id",
        "pipeline_version",
        "git_commit",
        "container_digest",
        "config_snapshot",
        "config_hash",
        "time_base",
        "is_variable_frame_rate",
    } <= columns


def test_evidence_references_are_scoped_to_the_same_job() -> None:
    detection = Base.metadata.tables["detections"]
    foreign_keys = {constraint.name for constraint in detection.foreign_key_constraints}
    assert "fk_detections_track_same_job" in foreign_keys
    assert "fk_detections_full_frame_same_job" in foreign_keys
    assert "fk_detections_crop_same_job" in foreign_keys


def test_verified_ground_truth_has_a_completeness_constraint() -> None:
    table = Base.metadata.tables["ground_truth_records"]
    constraints = {constraint.name for constraint in table.constraints}
    assert "ck_gt_verified_complete" in constraints
    assert "ck_gt_duplicate_consistent" in constraints

