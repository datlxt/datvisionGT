"""support motorcycle events and explicit no-plate output

Revision ID: 20260722_0002
Revises: 88e1ca936693
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_0002"
down_revision: str | None = "88e1ca936693"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("job_models", "role", type_=sa.String(32))
    op.drop_constraint("ck_job_model_role", "job_models", type_="check")
    op.create_check_constraint(
        "ck_job_model_role",
        "job_models",
        "role IN ('VEHICLE_DETECTOR', 'PLATE_DETECTOR', 'OCR', 'TRACKER')",
    )
    op.drop_constraint("ck_model_type", "model_versions", type_="check")
    op.create_check_constraint(
        "ck_model_type",
        "model_versions",
        "model_type IN ('VEHICLE_DETECTOR', 'PLATE_DETECTOR', 'PLATE_OCR', "
        "'PLATE_TRACKER', 'FACE_DETECTOR', 'FACE_RECOGNIZER', 'FACE_TRACKER')",
    )
    op.drop_constraint("ck_artifact_kind", "artifacts", type_="check")
    op.create_check_constraint(
        "ck_artifact_kind",
        "artifacts",
        "kind IN ('SOURCE_VIDEO', 'SOURCE_IMAGE', 'FULL_FRAME', 'VEHICLE_CROP', "
        "'PLATE_CROP', 'THUMBNAIL', 'EXPORT_XLSX', 'EXPORT_CSV', 'EXPORT_ZIP', 'LOG')",
    )
    op.drop_constraint("ck_tracks_object_type", "tracks", type_="check")
    op.create_check_constraint(
        "ck_tracks_object_type", "tracks", "object_type IN ('VEHICLE', 'PLATE', 'FACE')"
    )
    op.alter_column("tracks", "object_type", server_default="VEHICLE")
    op.drop_constraint("ck_detections_object_type", "detections", type_="check")
    op.create_check_constraint(
        "ck_detections_object_type",
        "detections",
        "object_type IN ('VEHICLE', 'PLATE', 'FACE')",
    )
    op.alter_column("processing_jobs", "processing_mode", server_default="BALANCED")


def downgrade() -> None:
    op.execute("DELETE FROM job_models WHERE role IN ('VEHICLE_DETECTOR', 'PLATE_DETECTOR')")
    op.drop_constraint("ck_job_model_role", "job_models", type_="check")
    op.create_check_constraint(
        "ck_job_model_role", "job_models", "role IN ('DETECTOR', 'OCR', 'TRACKER')"
    )
    op.alter_column("job_models", "role", type_=sa.String(16))
    op.execute("UPDATE detections SET object_type = 'PLATE' WHERE object_type = 'VEHICLE'")
    op.execute("UPDATE tracks SET object_type = 'PLATE' WHERE object_type = 'VEHICLE'")
    op.drop_constraint("ck_detections_object_type", "detections", type_="check")
    op.create_check_constraint(
        "ck_detections_object_type", "detections", "object_type IN ('PLATE', 'FACE')"
    )
    op.alter_column("tracks", "object_type", server_default="PLATE")
    op.drop_constraint("ck_tracks_object_type", "tracks", type_="check")
    op.create_check_constraint(
        "ck_tracks_object_type", "tracks", "object_type IN ('PLATE', 'FACE')"
    )
    op.drop_constraint("ck_artifact_kind", "artifacts", type_="check")
    op.create_check_constraint(
        "ck_artifact_kind",
        "artifacts",
        "kind IN ('SOURCE_VIDEO', 'SOURCE_IMAGE', 'FULL_FRAME', 'PLATE_CROP', "
        "'THUMBNAIL', 'EXPORT_XLSX', 'EXPORT_CSV', 'EXPORT_ZIP', 'LOG')",
    )
    op.drop_constraint("ck_model_type", "model_versions", type_="check")
    op.create_check_constraint(
        "ck_model_type",
        "model_versions",
        "model_type IN ('PLATE_DETECTOR', 'PLATE_OCR', 'PLATE_TRACKER', "
        "'FACE_DETECTOR', 'FACE_RECOGNIZER', 'FACE_TRACKER')",
    )
    op.alter_column("processing_jobs", "processing_mode", server_default="HIGH_RECALL")
