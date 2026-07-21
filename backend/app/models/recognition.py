import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import UUIDPrimaryKeyMixin


class RecognitionResult(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "recognition_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "track_id"],
            ["tracks.job_id", "tracks.id"],
            name="fk_recognition_track_same_job",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["job_id", "detection_id"],
            ["detections.job_id", "detections.id"],
            name="fk_recognition_detection_same_job",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "stage IN ('FRAME_OCR', 'TRACK_VOTE')", name="ck_recognition_stage"
        ),
        CheckConstraint(
            "confidence BETWEEN 0 AND 1", name="ck_recognition_confidence_range"
        ),
        CheckConstraint(
            "track_id IS NOT NULL OR detection_id IS NOT NULL",
            name="ck_recognition_has_parent",
        ),
        CheckConstraint(
            "jsonb_typeof(candidates) = 'array'", name="ck_recognition_candidates_array"
        ),
        CheckConstraint(
            "jsonb_typeof(raw_output) = 'object'", name="ck_recognition_raw_object"
        ),
        Index("ix_recognition_track_created", "track_id", "created_at"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    track_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    detection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    predicted_text: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_text: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_versions.id", ondelete="RESTRICT"), nullable=False
    )
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    raw_output: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

