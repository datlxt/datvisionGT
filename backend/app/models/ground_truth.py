import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import UUIDPrimaryKeyMixin


class GroundTruthRecord(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ground_truth_records"
    __table_args__ = (
        UniqueConstraint("job_id", "id", name="uq_gt_job_id_id"),
        UniqueConstraint("job_id", "record_code", name="uq_gt_job_record_code"),
        UniqueConstraint("job_id", "track_id", name="uq_gt_one_record_per_track"),
        ForeignKeyConstraint(
            ["job_id", "track_id"],
            ["tracks.job_id", "tracks.id"],
            name="fk_gt_track_same_job",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["job_id", "selected_detection_id"],
            ["detections.job_id", "detections.id"],
            name="fk_gt_detection_same_job",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["job_id", "duplicate_of_id"],
            ["ground_truth_records.job_id", "ground_truth_records.id"],
            name="fk_gt_duplicate_same_job",
            ondelete="RESTRICT",
        ),
        CheckConstraint("record_source IN ('MODEL', 'MANUAL')", name="ck_gt_record_source"),
        CheckConstraint(
            "verify_status IN ('UNVERIFIED', 'IN_REVIEW', 'VERIFIED', 'DISCARDED')",
            name="ck_gt_verify_status",
        ),
        CheckConstraint(
            "evidence_status IN ('PENDING', 'VALID', 'INVALID')",
            name="ck_gt_evidence_status",
        ),
        CheckConstraint(
            "prediction_confidence IS NULL OR prediction_confidence BETWEEN 0 AND 1",
            name="ck_gt_prediction_confidence_range",
        ),
        CheckConstraint(
            "jsonb_typeof(quality_flags) = 'array'", name="ck_gt_quality_flags_array"
        ),
        CheckConstraint("version >= 1", name="ck_gt_version_positive"),
        CheckConstraint(
            "(is_duplicate = false AND duplicate_of_id IS NULL) OR "
            "(is_duplicate = true AND duplicate_of_id IS NOT NULL)",
            name="ck_gt_duplicate_consistent",
        ),
        CheckConstraint(
            "duplicate_of_id IS NULL OR duplicate_of_id <> id",
            name="ck_gt_not_self_duplicate",
        ),
        CheckConstraint(
            "verify_status <> 'VERIFIED' OR "
            "(normalized_gt_text IS NOT NULL AND evidence_status = 'VALID' "
            "AND verified_by IS NOT NULL AND verified_at IS NOT NULL)",
            name="ck_gt_verified_complete",
        ),
        Index("ix_gt_job_verify_status", "job_id", "verify_status"),
        Index("ix_gt_job_evidence_status", "job_id", "evidence_status"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    track_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    selected_detection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    record_code: Mapped[str] = mapped_column(String(64), nullable=False)
    record_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="MODEL", server_default="MODEL"
    )
    predicted_text: Mapped[str | None] = mapped_column(String(64))
    prediction_confidence: Mapped[float | None] = mapped_column(Float)
    gt_text: Mapped[str | None] = mapped_column(String(64))
    normalized_gt_text: Mapped[str | None] = mapped_column(String(64))
    classification: Mapped[str | None] = mapped_column(String(64))
    quality_flags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    verify_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="UNVERIFIED", server_default="UNVERIFIED"
    )
    evidence_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PENDING", server_default="PENDING"
    )
    is_duplicate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    note: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    verified_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ReviewAction(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "review_actions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "ground_truth_record_id"],
            ["ground_truth_records.job_id", "ground_truth_records.id"],
            name="fk_review_gt_same_job",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "action IN ('CREATE', 'EDIT', 'VERIFY', 'DISCARD', 'RESTORE', "
            "'MARK_DUPLICATE', 'UNMARK_DUPLICATE')",
            name="ck_review_action",
        ),
        CheckConstraint("jsonb_typeof(before_state) = 'object'", name="ck_review_before_object"),
        CheckConstraint("jsonb_typeof(after_state) = 'object'", name="ck_review_after_object"),
        Index("ix_review_gt_created", "ground_truth_record_id", "created_at"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    ground_truth_record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    before_state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    after_state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
