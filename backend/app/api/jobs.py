from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict
from redis import Redis
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job as RqJob
from rq.registry import FailedJobRegistry
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import get_settings
from app.db.session import SessionLocal, get_db
from app.models import JobEvent, ProcessingJob
from app.vision.media import PyAVVideoReader
from app.workers import live
from app.workers.callbacks import mark_job_failed

router = APIRouter(prefix="/jobs", tags=["jobs"])
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}
VEHICLE_TYPES = {"motorcycle", "car"}
LANE_DIRECTIONS = {"up", "down", "left", "right"}


class StartJobRequest(BaseModel):
    """Optional per-lane setup captured on the config screen before processing starts."""

    # Normalized [x1, y1, x2, y2] in 0..1 — restrict detection to one lane so the pipeline
    # never fires on adjacent lanes / background.
    roi: list[float] | None = None
    # Travel direction of the lane; flags tracks moving against it for a human to check.
    lane_direction: str | None = None
    # Frames analysed per second of video. Clamped server-side so a bad client value can't reach
    # the extractor (0 / negative would divide-by-zero; huge values would freeze it).
    sample_rate: float | None = None


def _sanitize_roi(roi: list[float] | None) -> list[float] | None:
    """Clamp to 0..1 and order corners; reject a degenerate (too small) rectangle."""

    if not roi or len(roi) != 4:
        return None
    x1, y1, x2, y2 = (max(0.0, min(1.0, float(v))) for v in roi)
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    if x2 - x1 < 0.02 or y2 - y1 < 0.02:
        return None
    return [round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)]


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
    vehicle_type: str
    flagged: bool
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
    vehicle_type: str = "motorcycle",
) -> ProcessingJob:
    settings = get_settings()
    filename = _safe_filename(unquote(filename_header))
    extension = Path(filename).suffix.lower()
    if extension not in VIDEO_EXTENSIONS:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Unsupported video format")
    if vehicle_type not in VEHICLE_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unsupported vehicle type")

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
        # 2 fps: a plate is visible ~2-3s so this still yields 4-6 reads per pass (enough for the
        # vote + best-frame pick) while ~halving the detection frames — the main speed lever.
        sample_rate=2.0,
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
            "vehicle_type": vehicle_type,
            "vehicle_detector": "yolox-tiny",
            "plate_detector": "yolo-v9-t-512-license-plate-end2end",
            "ocr": "cct-xs-v2-global-model",
            "postprocess": (
                "quality-gate+multi-frame-vote+best-frame-exposure"
                "+near-plate-merge+global-plate-dedupe+motion-suppression"
            ),
            "sample_rate": 2.0,
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


@router.post(
    "/from-condense/{condense_id}",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_job_from_condense(
    condense_id: str,
    session: Annotated[Session, Depends(get_db)],
    vehicle_type: str = "motorcycle",
) -> ProcessingJob:
    """Create a DRAFT job from an ALREADY-CONDENSED video (no re-upload). The reviewer then sets
    the ROI / lane and starts it through the normal flow — closing the cut → GT loop end-to-end."""

    settings = get_settings()
    if vehicle_type not in VEHICLE_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unsupported vehicle type")
    condensed = settings.storage_root / "condense" / condense_id / "condensed.mp4"
    if not condensed.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Condensed video not found")

    upload_meta: dict = {}
    upload_json = settings.storage_root / "condense" / condense_id / "upload.json"
    if upload_json.is_file():
        upload_meta = json.loads(upload_json.read_text())
    source_stem = Path(upload_meta.get("source_name", "video.mp4")).stem
    filename = _safe_filename(f"cut_{source_stem}.mp4")

    upload_id = uuid.uuid4()
    upload_dir = settings.storage_root / "uploads" / str(upload_id)
    upload_dir.mkdir(parents=True, exist_ok=False)
    final_path = upload_dir / filename
    shutil.copyfile(condensed, final_path)
    metadata = PyAVVideoReader().probe(final_path)

    created = datetime.now(UTC)
    job = ProcessingJob(
        job_code=f"JOB_{created:%Y%m%d_%H%M%S}_{str(upload_id)[:8].upper()}",
        source_name=filename,
        source_path=final_path.relative_to(settings.storage_root).as_posix(),
        source_hash=metadata.sha256,
        source_size_bytes=metadata.size_bytes,
        source_mime_type=metadata.mime_type,
        object_mode="PLATE",
        processing_mode="BALANCED",
        sample_rate=2.0,
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
            "vehicle_type": vehicle_type,
            "vehicle_detector": "yolox-tiny",
            "plate_detector": "yolo-v9-t-512-license-plate-end2end",
            "ocr": "cct-xs-v2-global-model",
            "postprocess": (
                "quality-gate+multi-frame-vote+best-frame-exposure"
                "+near-plate-merge+global-plate-dedupe+motion-suppression"
            ),
            "sample_rate": 2.0,
            "result_classes": ["RECOGNIZED", "LOW_CONFIDENCE", "UNREADABLE", "NO_PLATE"],
            "condensed_from": condense_id,
        },
    )
    session.add(job)
    session.flush()
    session.add(
        JobEvent(
            job_id=job.id,
            event_type="VIDEO_UPLOADED",
            message="Condensed video imported as a draft job",
            data={"condensed_from": condense_id},
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


@router.get("/{job_id}/live")
def stream_live_preview(job_id: uuid.UUID) -> StreamingResponse:
    """Server-Sent Events stream of the worker's live processing preview (see workers/live.py).

    Reads the latest annotated snapshot the worker publishes to Redis and forwards it to the
    browser. Stops when the job leaves a processing state. Read-only, best-effort — no DB writes.
    """

    settings = get_settings()
    key = live.live_key(str(job_id))
    _ACTIVE = ("DRAFT", "PENDING", "QUEUED", "PROCESSING")

    def generator():
        conn = Redis.from_url(settings.redis_url)
        last: bytes | None = None
        ticks = 0
        # Stream up to ~20 min per connection, then just RETURN (no "done") so the browser's
        # EventSource silently reconnects and continues — long videos take longer than any single
        # connection, and the old 6-min "done" cap froze the live view mid-way. Only a genuinely
        # finished job ends the stream with "done".
        # Poll fast (~12/s) so the smoother, higher-fps preview is delivered without stutter.
        for _ in range(15000):  # 15000 * 0.08s ≈ 20 min per connection
            data = conn.get(key)
            if data is not None and data != last:
                last = data
                yield f"data: {data.decode('utf-8')}\n\n"
            ticks += 1
            if ticks % 25 == 0:  # ~every 2s: end the stream once processing has finished
                with SessionLocal() as check:
                    job = check.get(ProcessingJob, job_id)
                    if job is None or job.status not in _ACTIVE:
                        yield "event: done\ndata: {}\n\n"
                        return
            time.sleep(0.08)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{job_id}/start", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
def start_job(
    job_id: uuid.UUID,
    session: Annotated[Session, Depends(get_db)],
    payload: Annotated[StartJobRequest | None, Body()] = None,
) -> ProcessingJob:
    job = session.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if job.status != "DRAFT":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Job cannot start from {job.status}")

    # Per-lane ROI + direction drawn on the config screen. Stored so the worker restricts
    # detection to this lane and flags wrong-direction tracks. Absent → pipeline default (no
    # change for lanes configured without them).
    if payload is not None:
        config = dict(job.config_snapshot or {})
        roi = _sanitize_roi(payload.roi)
        if roi is not None:
            config["roi"] = roi
        if payload.lane_direction in LANE_DIRECTIONS:
            config["lane_direction"] = payload.lane_direction
        job.config_snapshot = config
        flag_modified(job, "config_snapshot")
        if payload.sample_rate is not None:
            job.sample_rate = max(1.0, min(12.0, float(payload.sample_rate)))

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


class FlagRequest(BaseModel):
    flagged: bool


@router.post("/{job_id}/flag", response_model=JobResponse)
def set_job_flag(
    job_id: uuid.UUID,
    payload: FlagRequest,
    session: Annotated[Session, Depends(get_db)],
) -> ProcessingJob:
    """Toggle the reviewer's "important — remember this" bookmark. Stored in the config snapshot
    (no schema change) and surfaced as ``flagged`` on the job; it never touches GT or export data."""

    job = session.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    config = dict(job.config_snapshot or {})
    config["flagged"] = payload.flagged
    job.config_snapshot = config
    flag_modified(job, "config_snapshot")
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
