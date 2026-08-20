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
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import UUIDPrimaryKeyMixin


class ProcessingJob(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT', 'PENDING', 'QUEUED', 'PROCESSING', "
            "'WAITING_FOR_REVIEW', 'COMPLETED', 'FAILED', 'CANCELLED')",
            name="ck_processing_jobs_status",
        ),
        CheckConstraint("object_mode IN ('PLATE', 'FACE', 'BOTH')", name="ck_jobs_object_mode"),
        CheckConstraint(
            "processing_mode IN ('HIGH_RECALL', 'BALANCED', 'FAST')",
            name="ck_jobs_processing_mode",
        ),
        CheckConstraint("sample_rate > 0", name="ck_jobs_sample_rate_positive"),
        CheckConstraint("progress BETWEEN 0 AND 100", name="ck_jobs_progress_range"),
        CheckConstraint(
            "processed_frames >= 0 AND (total_frames IS NULL OR total_frames >= 0)",
            name="ck_jobs_frame_counts_nonnegative",
        ),
        CheckConstraint(
            "source_hash IS NULL OR length(source_hash) = 64", name="ck_jobs_source_hash_length"
        ),
        CheckConstraint(
            "config_hash IS NULL OR length(config_hash) = 64", name="ck_jobs_config_hash_length"
        ),
        CheckConstraint(
            "jsonb_typeof(config_snapshot) = 'object'", name="ck_jobs_config_snapshot_object"
        ),
        CheckConstraint("source_type IN ('VIDEO', 'IMAGE_SET')", name="ck_jobs_source_type"),
        CheckConstraint(
            "source_size_bytes IS NULL OR source_size_bytes >= 0",
            name="ck_jobs_source_size_nonnegative",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="ck_jobs_duration_nonnegative"
        ),
        CheckConstraint(
            "(width IS NULL AND height IS NULL) OR (width > 0 AND height > 0)",
            name="ck_jobs_dimensions_valid",
        ),
        CheckConstraint("fps IS NULL OR fps > 0", name="ck_jobs_fps_positive"),
        Index("ix_processing_jobs_created_at", "created_at"),
        Index("ix_processing_jobs_source_hash", "source_hash"),
    )

    job_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    source_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="VIDEO", server_default="VIDEO"
    )
    source_name: Mapped[str] = mapped_column(String(512), nullable=False)
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_hash: Mapped[str | None] = mapped_column(String(64))
    source_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    source_mime_type: Mapped[str | None] = mapped_column(String(127))
    camera_config_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("camera_configs.id", name="fk_jobs_camera_config", ondelete="SET NULL"),
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_jobs_created_by", ondelete="SET NULL"),
    )
    object_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="PLATE")
    processing_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="BALANCED", server_default="BALANCED"
    )
    sample_rate: Mapped[float] = mapped_column(
        Float, nullable=False, default=4.0, server_default="4.0"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT", index=True)
    current_stage: Mapped[str | None] = mapped_column(String(64))
    processed_frames: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_frames: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    codec: Mapped[str | None] = mapped_column(String(64))
    time_base: Mapped[str | None] = mapped_column(String(32))
    is_variable_frame_rate: Mapped[bool | None] = mapped_column(Boolean)
    progress: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0.0"
    )
    pipeline_version: Mapped[str | None] = mapped_column(String(64))
    git_commit: Mapped[str | None] = mapped_column(String(64))
    container_digest: Mapped[str | None] = mapped_column(String(255))
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    config_hash: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @property
    def vehicle_type(self) -> str:
        """Selectable vehicle class stored in the config snapshot (default motorcycle)."""

        return (self.config_snapshot or {}).get("vehicle_type", "motorcycle")

    @property
    def flagged(self) -> bool:
        """Reviewer's "important — remember this" mark, kept in the config snapshot (no schema
        change). Purely a UI bookmark; it never affects processing, GT or export contents."""

        return bool((self.config_snapshot or {}).get("flagged", False))
