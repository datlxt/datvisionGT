from __future__ import annotations

import hashlib
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from redis import Redis
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job as RqJob
from rq.registry import FailedJobRegistry
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models import JobEvent, ProcessingJob
from app.vision.media import PyAVVideoReader
from app.workers.callbacks import mark_job_failed

router = APIRouter(prefix="/jobs", tags=["jobs"])
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_code: str
    source_name: str
    source_hash: str | None
    source_size_bytes: int | None
    status: str
    current_stage: str | None
    progress: float
    processed_frames: int
    total_frames: int | None
    duration_ms: int | None
    width: int | None
    height: int | None
    fps: float | None
    processing_mode: str
    sample_rate: float
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


def _safe_filename(filename: str | None) -> str:
    original = Path(filename or "video.mp4").name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(original).stem).strip("._") or "video"
    suffix = Path(original).suffix.lower()
    return f"{stem[:120]}{suffix}"


def _enqueue_job(queue: Queue, job: ProcessingJob) -> None:
    queue.enqueue(
        "app.workers.pipeline.process_plate_job",
        str(job.id),
        job_id=str(job.id),
        result_ttl=86400,
        failure_ttl=604800,
        job_timeout="6h",
        on_failure=mark_job_failed,
    )


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    request: Request,
    filename_header: Annotated[str, Header(alias="X-Filename")],
    session: Annotated[Session, Depends(get_db)],
) -> ProcessingJob:
    settings = get_settings()
    filename = _safe_filename(filename_header)
    extension = Path(filename).suffix.lower()
    if extension not in VIDEO_EXTENSIONS:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Unsupported video format")

    upload_id = uuid.uuid4()
    upload_dir = settings.storage_root / "uploads" / str(upload_id)
    upload_dir.mkdir(parents=True, exist_ok=False)
    final_path = upload_dir / filename
    partial_path = upload_dir / f"{filename}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        with partial_path.open("xb") as destination:
            async for chunk in request.stream():
                if not chunk:
                    continue
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Video exceeds 2 GB"
                    )
                digest.update(chunk)
                destination.write(chunk)
        partial_path.replace(final_path)
        metadata = PyAVVideoReader().probe(final_path, sha256=digest.hexdigest())
    except HTTPException:
        partial_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        upload_dir.rmdir()
        raise
    except Exception as exc:
        partial_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        upload_dir.rmdir()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid video: {exc}") from exc
    created = datetime.now(UTC)
    job = ProcessingJob(
        job_code=f"JOB_{created:%Y%m%d_%H%M%S}_{str(upload_id)[:8].upper()}",
        source_name=filename,
        source_path=final_path.relative_to(settings.storage_root).as_posix(),
        source_hash=metadata.sha256,
        source_size_bytes=metadata.size_bytes,
        source_mime_type=request.headers.get("content-type") or metadata.mime_type,
        object_mode="PLATE",
        processing_mode="BALANCED",
        sample_rate=4.0,
        status="DRAFT",
        current_stage="UPLOADED",
        progress=0.0,
        total_frames=metadata.frame_count,
        duration_ms=metadata.duration_ms,
        width=metadata.width,
        height=metadata.height,
        fps=metadata.fps,
        codec=metadata.codec,
        time_base=metadata.time_base,
        pipeline_version="motorcycle-alpr-v4",
        config_snapshot={
            "camera": "rear_toll_lane",
            "mode": "STANDARD",
            "vehicle_detector": "yolox-tiny",
            "plate_detector": "yolo-v9-t-512-license-plate-end2end",
            "ocr": "cct-xs-v2-global-model",
            "postprocess": (
                "quality-gate+multi-frame-vote+best-frame-exposure"
                "+near-plate-merge+global-plate-dedupe+motion-suppression"
            ),
            "sample_rate": 4.0,
            "result_classes": ["RECOGNIZED", "LOW_CONFIDENCE", "UNREADABLE", "NO_PLATE"],
        },
    )
    session.add(job)
    session.flush()
    session.add(
        JobEvent(
            job_id=job.id,
            event_type="VIDEO_UPLOADED",
            message="Video uploaded and probed successfully",
            data={"sha256": metadata.sha256, "size_bytes": metadata.size_bytes},
        )
    )
    session.commit()
    session.refresh(job)
    return job


@router.get("", response_model=list[JobResponse])
def list_jobs(session: Annotated[Session, Depends(get_db)]) -> list[ProcessingJob]:
    return list(
        session.scalars(select(ProcessingJob).order_by(ProcessingJob.created_at.desc())).all()
    )


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: uuid.UUID, session: Annotated[Session, Depends(get_db)]) -> ProcessingJob:
    job = session.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


@router.get("/{job_id}/source", response_class=FileResponse)
def get_job_source(
    job_id: uuid.UUID, session: Annotated[Session, Depends(get_db)]
) -> FileResponse:
    job = session.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    settings = get_settings()
    root = settings.storage_root.resolve()
    path = (root / job.source_path).resolve(strict=True)
    if not path.is_relative_to(root):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid source path")
    return FileResponse(path, media_type=job.source_mime_type or "application/octet-stream")


@router.post("/{job_id}/start", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
def start_job(job_id: uuid.UUID, session: Annotated[Session, Depends(get_db)]) -> ProcessingJob:
    job = session.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if job.status != "DRAFT":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Job cannot start from {job.status}")

    settings = get_settings()
    queue = Queue(settings.rq_queue, connection=Redis.from_url(settings.redis_url))
    _enqueue_job(queue, job)
    job.status = "QUEUED"
    job.current_stage = "QUEUED"
    session.add(JobEvent(job_id=job.id, event_type="JOB_QUEUED", message="Inference job queued"))
    session.commit()
    session.refresh(job)
    return job


@router.post("/{job_id}/retry", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_job(job_id: uuid.UUID, session: Annotated[Session, Depends(get_db)]) -> ProcessingJob:
    """Resume an interrupted/failed idempotent pipeline without re-uploading its video."""

    job = session.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if job.status not in {"FAILED", "PROCESSING", "QUEUED", "CANCELLED"}:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Job cannot retry from {job.status}")

    settings = get_settings()
    connection = Redis.from_url(settings.redis_url)
    queue = Queue(settings.rq_queue, connection=connection)
    try:
        rq_job = RqJob.fetch(str(job.id), connection=connection)
        rq_status = rq_job.get_status(refresh=True)
    except NoSuchJobError:
        rq_job = None
        rq_status = None

    if rq_status in {"queued", "started", "deferred", "scheduled"}:
        raise HTTPException(status.HTTP_409_CONFLICT, f"RQ job is already {rq_status}")
    registry = FailedJobRegistry(queue=queue)
    if rq_job is not None and str(job.id) in registry.get_job_ids():
        registry.requeue(str(job.id))
    elif rq_job is None:
        _enqueue_job(queue, job)
    else:
        raise HTTPException(status.HTTP_409_CONFLICT, f"RQ job cannot retry from {rq_status}")

    job.status = "QUEUED"
    job.current_stage = "RESUMING"
    job.error_code = None
    job.error_message = None
    job.cancel_requested_at = None
    job.completed_at = None
    session.add(
        JobEvent(
            job_id=job.id,
            event_type="JOB_REQUEUED",
            message="Interrupted job requeued without uploading the source again",
        )
    )
    session.commit()
    session.refresh(job)
    return job


@router.post("/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: uuid.UUID, session: Annotated[Session, Depends(get_db)]) -> ProcessingJob:
    job = session.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if job.status in {"COMPLETED", "FAILED", "CANCELLED"}:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Job is already {job.status}")
    job.cancel_requested_at = datetime.now(UTC)
    if job.status in {"DRAFT", "QUEUED"}:
        job.status = "CANCELLED"
    session.commit()
    session.refresh(job)
    return job


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: uuid.UUID, session: Annotated[Session, Depends(get_db)]) -> Response:
    """Delete a job with all its DB records (cascade) and stored evidence/uploads."""

    job = session.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if job.status in {"QUEUED", "PROCESSING"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Hủy job trước khi xóa")

    settings = get_settings()
    root = settings.storage_root.resolve()
    removable: list[Path] = []
    if job.source_path:
        upload_dir = (root / job.source_path).resolve().parent
        if upload_dir.is_relative_to(root) and upload_dir != root:
            removable.append(upload_dir)
    job_dir = (root / "jobs" / str(job.id)).resolve()
    if job_dir.is_relative_to(root):
        removable.append(job_dir)

    try:  # Best effort: drop any lingering RQ job so a deleted id is not reprocessed.
        rq_job = RqJob.fetch(str(job.id), connection=Redis.from_url(settings.redis_url))
        rq_job.delete()
    except Exception:
        pass

    session.delete(job)
    session.commit()
    for directory in removable:
        shutil.rmtree(directory, ignore_errors=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
