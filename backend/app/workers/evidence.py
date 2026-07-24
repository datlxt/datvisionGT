from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import Artifact, JobEvent, ProcessingJob
from app.vision.media import EvidenceExtractor


class EvidenceCancelled(RuntimeError):
    pass


def _resolve_source(storage_root: Path, source_path: str) -> Path:
    root = storage_root.resolve()
    candidate = Path(source_path)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("Job source_path must be inside STORAGE_ROOT")
    return resolved


def _config_hash(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _upsert_artifact(session: Any, job_id: uuid.UUID, values: dict[str, Any]) -> None:
    artifact = session.scalar(
        select(Artifact).where(
            Artifact.job_id == job_id,
            Artifact.storage_key == values["storage_key"],
        )
    )
    if artifact is None:
        session.add(Artifact(job_id=job_id, **values))
        return
    for name, value in values.items():
        setattr(artifact, name, value)


def process_evidence_job(job_id: str) -> dict[str, Any]:
    """RQ task: extract evidence and persist reproducibility metadata in PostgreSQL."""

    settings = get_settings()
    parsed_job_id = uuid.UUID(job_id)
    with SessionLocal() as session:
        job = session.get(ProcessingJob, parsed_job_id)
        if job is None:
            raise ValueError(f"Processing job does not exist: {job_id}")

        source = _resolve_source(settings.storage_root, job.source_path)
        job.status = "PROCESSING"
        job.current_stage = "EVIDENCE_EXTRACT"
        job.started_at = job.started_at or datetime.now(UTC)
        job.progress = max(job.progress, 1.0)
        session.commit()

        try:
            def update_progress(decoded_frames: int, total_frames: int | None) -> None:
                session.refresh(job)
                if job.cancel_requested_at is not None:
                    raise EvidenceCancelled("Evidence extraction was cancelled")
                job.processed_frames = decoded_frames
                job.total_frames = total_frames
                if total_frames:
                    job.progress = min(19.0, 1.0 + decoded_frames / total_frames * 18.0)
                session.commit()

            manifest = EvidenceExtractor().extract(
                source_path=source,
                storage_root=settings.storage_root,
                job_id=str(job.id),
                sample_rate=job.sample_rate,
                progress_callback=update_progress,
            )

            source_key = source.relative_to(settings.storage_root.resolve()).as_posix()
            _upsert_artifact(
                session,
                job.id,
                {
                    "kind": "SOURCE_VIDEO",
                    "storage_key": source_key,
                    "sha256": manifest.source.sha256,
                    "mime_type": manifest.source.mime_type,
                    "size_bytes": manifest.source.size_bytes,
                    "width": manifest.source.width,
                    "height": manifest.source.height,
                    "frame_number": None,
                    "pts": None,
                    "timestamp_ms": None,
                    "metadata_": {
                        "codec": manifest.source.codec,
                        "duration_ms": manifest.source.duration_ms,
                        "fps": manifest.source.fps,
                        "time_base": manifest.source.time_base,
                    },
                },
            )
            for frame in manifest.frames:
                _upsert_artifact(
                    session,
                    job.id,
                    {
                        "kind": "FULL_FRAME",
                        "storage_key": frame.storage_key,
                        "sha256": frame.sha256,
                        "mime_type": "image/jpeg",
                        "size_bytes": frame.size_bytes,
                        "width": frame.width,
                        "height": frame.height,
                        "frame_number": frame.frame_index,
                        "pts": frame.pts,
                        "timestamp_ms": frame.timestamp_ms,
                        "metadata_": {
                            "timestamp_us": frame.timestamp_us,
                            "target_timestamp_us": frame.target_timestamp_us,
                        },
                    },
                )

            config = {
                **job.config_snapshot,
                "evidence": {
                    "pipeline_version": manifest.pipeline_version,
                    "sample_rate": manifest.sample_rate,
                    "jpeg_quality": 92,
                },
            }
            job.source_hash = manifest.source.sha256
            job.source_size_bytes = manifest.source.size_bytes
            job.source_mime_type = manifest.source.mime_type
            job.duration_ms = manifest.source.duration_ms
            job.width = manifest.source.width
            job.height = manifest.source.height
            job.fps = manifest.source.fps
            job.codec = manifest.source.codec
            job.time_base = manifest.source.time_base
            job.is_variable_frame_rate = manifest.source.is_variable_frame_rate
            job.total_frames = manifest.source.frame_count
            job.processed_frames = manifest.decoded_frame_count
            job.config_snapshot = config
            job.config_hash = _config_hash(config)
            job.status = "PENDING"
            job.current_stage = "EVIDENCE_READY"
            job.progress = 20.0
            session.add(
                JobEvent(
                    job_id=job.id,
                    event_type="EVIDENCE_READY",
                    message="Timestamp-aware evidence extraction completed",
                    data={
                        "sampled_frames": len(manifest.frames),
                        "decoded_frames": manifest.decoded_frame_count,
                        "manifest_path": manifest.manifest_path.relative_to(
                            settings.storage_root.resolve()
                        ).as_posix(),
                    },
                )
            )
            session.commit()
            return {
                "job_id": str(job.id),
                "status": job.status,
                "sampled_frames": len(manifest.frames),
                "manifest_path": str(manifest.manifest_path),
            }
        except Exception as exc:
            session.rollback()
            failed_job = session.get(ProcessingJob, parsed_job_id)
            if failed_job is not None:
                cancelled = isinstance(exc, EvidenceCancelled)
                failed_job.status = "CANCELLED" if cancelled else "FAILED"
                failed_job.current_stage = "EVIDENCE_EXTRACT"
                failed_job.error_code = None if cancelled else "EVIDENCE_EXTRACTION_FAILED"
                failed_job.error_message = None if cancelled else str(exc)[:4000]
                session.add(
                    JobEvent(
                        job_id=failed_job.id,
                        event_type="EVIDENCE_CANCELLED" if cancelled else "EVIDENCE_FAILED",
                        level="WARNING" if cancelled else "ERROR",
                        message=(
                            "Evidence extraction cancelled"
                            if cancelled
                            else "Evidence extraction failed"
                        ),
                        data={"error": str(exc)[:1000]},
                    )
                )
                session.commit()
            raise
