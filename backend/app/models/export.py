import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import UUIDPrimaryKeyMixin


class Export(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "exports"
    __table_args__ = (
        CheckConstraint(
            "export_type IN ('GT_DRAFT', 'GT_FINAL', 'SUMMARY', 'CROP_ZIP')",
            name="ck_exports_type",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED')",
            name="ck_exports_status",
        ),
        CheckConstraint("record_count >= 0", name="ck_exports_record_count_nonnegative"),
        CheckConstraint("jsonb_typeof(config_snapshot) = 'object'", name="ck_export_config_object"),
        ForeignKeyConstraint(
            ["job_id", "artifact_id"],
            ["artifacts.job_id", "artifacts.id"],
            name="fk_exports_artifact_same_job",
            ondelete="RESTRICT",
        ),
        Index("ix_exports_job_created", "job_id", "created_at"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    export_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PENDING", server_default="PENDING"
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True)
    record_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    error_message: Mapped[str | None] = mapped_column(String(1024))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
