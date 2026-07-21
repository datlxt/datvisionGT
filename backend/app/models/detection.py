import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import UUIDPrimaryKeyMixin


class Detection(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "detections"
    __table_args__ = (
        UniqueConstraint("job_id", "id", name="uq_detections_job_id_id"),
        ForeignKeyConstraint(
            ["job_id", "track_id"],
            ["tracks.job_id", "tracks.id"],
            name="fk_detections_track_same_job",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["job_id", "full_frame_artifact_id"],
            ["artifacts.job_id", "artifacts.id"],
            name="fk_detections_full_frame_same_job",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["job_id", "crop_artifact_id"],
            ["artifacts.job_id", "artifacts.id"],
            name="fk_detections_crop_same_job",
            ondelete="RESTRICT",
        ),
        CheckConstraint("object_type IN ('PLATE', 'FACE')", name="ck_detections_object_type"),
        CheckConstraint("source IN ('MODEL', 'MANUAL')", name="ck_detections_source"),
        CheckConstraint("frame_number >= 0", name="ck_detections_frame_nonnegative"),
        CheckConstraint("timestamp_ms >= 0", name="ck_detections_time_nonnegative"),
        CheckConstraint(
            "bbox_x1 >= 0 AND bbox_y1 >= 0 AND bbox_x2 > bbox_x1 AND bbox_y2 > bbox_y1",
            name="ck_detections_bbox_valid",
        ),
        CheckConstraint(
            "detection_confidence BETWEEN 0 AND 1",
            name="ck_detections_confidence_range",
        ),
        CheckConstraint(
            "quality_score IS NULL OR quality_score BETWEEN 0 AND 1",
            name="ck_detections_quality_range",
        ),
        CheckConstraint(
            "evidence_status IN ('PENDING', 'VALID', 'INVALID')",
            name="ck_detections_evidence_status",
        ),
        CheckConstraint("jsonb_typeof(raw_output) = 'object'", name="ck_detection_raw_object"),
        Index("ix_detections_job_frame", "job_id", "frame_number"),
        Index("ix_detections_track", "track_id"),
        Index(
            "uq_detections_one_best_per_track",
            "track_id",
            unique=True,
            postgresql_where=text("is_best = true"),
        ),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    track_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    object_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PLATE", server_default="PLATE"
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="MODEL", server_default="MODEL"
    )
    frame_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pts: Mapped[int | None] = mapped_column(BigInteger)
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bbox_x1: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_y1: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_x2: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_y2: Mapped[int] = mapped_column(Integer, nullable=False)
    detection_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Float)
    is_best: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    evidence_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PENDING", server_default="PENDING"
    )
    full_frame_artifact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    crop_artifact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    model_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_versions.id", ondelete="RESTRICT")
    )
    raw_output: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
