import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import UUIDPrimaryKeyMixin


class Track(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "tracks"
    __table_args__ = (
        UniqueConstraint("job_id", "id", name="uq_tracks_job_id_id"),
        UniqueConstraint("job_id", "track_code", name="uq_tracks_job_track_code"),
        UniqueConstraint("job_id", "event_key", name="uq_tracks_job_event_key"),
        ForeignKeyConstraint(
            ["job_id", "duplicate_of_id"],
            ["tracks.job_id", "tracks.id"],
            name="fk_tracks_duplicate_same_job",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "object_type IN ('VEHICLE', 'PLATE', 'FACE')", name="ck_tracks_object_type"
        ),
        CheckConstraint(
            "status IN ('OPEN', 'CLOSED', 'READY_FOR_REVIEW', 'DISCARDED')",
            name="ck_tracks_status",
        ),
        CheckConstraint(
            "evidence_status IN ('PENDING', 'VALID', 'INVALID')",
            name="ck_tracks_evidence_status",
        ),
        CheckConstraint(
            "start_frame >= 0 AND end_frame >= start_frame", name="ck_tracks_frame_range"
        ),
        CheckConstraint(
            "start_timestamp_ms >= 0 AND end_timestamp_ms >= start_timestamp_ms",
            name="ck_tracks_time_range",
        ),
        CheckConstraint(
            "quality_score IS NULL OR quality_score BETWEEN 0 AND 1",
            name="ck_tracks_quality_range",
        ),
        CheckConstraint(
            "duplicate_of_id IS NULL OR duplicate_of_id <> id",
            name="ck_tracks_not_self_duplicate",
        ),
        CheckConstraint("length(event_key) = 64", name="ck_tracks_event_key_length"),
        Index("ix_tracks_job_status", "job_id", "status"),
        Index("ix_tracks_job_start_time", "job_id", "start_timestamp_ms"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    track_code: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="VEHICLE", server_default="VEHICLE"
    )
    start_frame: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_frame: Mapped[int] = mapped_column(BigInteger, nullable=False)
    start_timestamp_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_timestamp_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Float)
    classification: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="OPEN", server_default="OPEN"
    )
    evidence_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PENDING", server_default="PENDING"
    )
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    event_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
