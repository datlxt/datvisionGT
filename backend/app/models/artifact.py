import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import UUIDPrimaryKeyMixin


class Artifact(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("job_id", "id", name="uq_artifacts_job_id_id"),
        UniqueConstraint("job_id", "storage_key", name="uq_artifact_job_storage_key"),
        CheckConstraint(
            "kind IN ('SOURCE_VIDEO', 'SOURCE_IMAGE', 'FULL_FRAME', 'VEHICLE_CROP', 'PLATE_CROP', "
            "'THUMBNAIL', 'EXPORT_XLSX', 'EXPORT_CSV', 'EXPORT_ZIP', 'LOG')",
            name="ck_artifact_kind",
        ),
        CheckConstraint("size_bytes >= 0", name="ck_artifact_size_nonnegative"),
        CheckConstraint(
            "sha256 IS NULL OR length(sha256) = 64", name="ck_artifact_sha256_length"
        ),
        CheckConstraint(
            "frame_number IS NULL OR frame_number >= 0", name="ck_artifact_frame_nonnegative"
        ),
        CheckConstraint(
            "timestamp_ms IS NULL OR timestamp_ms >= 0", name="ck_artifact_time_nonnegative"
        ),
        CheckConstraint("jsonb_typeof(metadata) = 'object'", name="ck_artifact_metadata_object"),
        Index("ix_artifacts_job_kind", "job_id", "kind"),
        Index("ix_artifacts_sha256", "sha256"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    mime_type: Mapped[str | None] = mapped_column(String(127))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    frame_number: Mapped[int | None] = mapped_column(BigInteger)
    pts: Mapped[int | None] = mapped_column(BigInteger)
    timestamp_ms: Mapped[int | None] = mapped_column(BigInteger)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
