import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class CameraConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "camera_configs"
    __table_args__ = (
        CheckConstraint("default_sample_rate > 0", name="ck_camera_sample_rate_positive"),
        CheckConstraint(
            "face_threshold BETWEEN 0 AND 1", name="ck_camera_face_threshold_range"
        ),
        CheckConstraint(
            "plate_threshold BETWEEN 0 AND 1", name="ck_camera_plate_threshold_range"
        ),
        CheckConstraint("jsonb_typeof(roi_config) = 'object'", name="ck_camera_roi_object"),
        CheckConstraint(
            "jsonb_typeof(tracking_config) = 'object'", name="ck_camera_tracking_object"
        ),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    camera_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    roi_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    default_sample_rate: Mapped[float] = mapped_column(
        Float, nullable=False, default=4.0, server_default="4.0"
    )
    face_threshold: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5, server_default="0.5"
    )
    plate_threshold: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5, server_default="0.5"
    )
    tracking_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class ModelVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("model_type", "name", "version", name="uq_model_identity"),
        CheckConstraint(
            "model_type IN ('VEHICLE_DETECTOR', 'PLATE_DETECTOR', 'PLATE_OCR', 'PLATE_TRACKER', "
            "'FACE_DETECTOR', 'FACE_RECOGNIZER', 'FACE_TRACKER')",
            name="ck_model_type",
        ),
        CheckConstraint(
            "file_sha256 IS NULL OR length(file_sha256) = 64",
            name="ck_model_sha256_length",
        ),
        CheckConstraint("jsonb_typeof(config) = 'object'", name="ck_model_config_object"),
    )

    model_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ONNX", server_default="ONNX"
    )
    file_path: Mapped[str | None] = mapped_column(String(1024))
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class JobModel(Base):
    __tablename__ = "job_models"
    __table_args__ = (
        UniqueConstraint("job_id", "role", name="uq_job_model_role"),
        CheckConstraint(
            "role IN ('VEHICLE_DETECTOR', 'PLATE_DETECTOR', 'OCR', 'TRACKER')",
            name="ck_job_model_role",
        ),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_versions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
