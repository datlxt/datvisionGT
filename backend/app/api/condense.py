from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from redis import Redis
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job as RqJob
from sqlalchemy.orm import Session

from app.api.jobs import VIDEO_EXTENSIONS, _safe_filename
from app.core.config import get_settings
from app.db.session import get_db
from app.models import JobEvent, ProcessingJob
from app.vision.media import PyAVVideoReader
from app.workers.callbacks import mark_job_failed

router = APIRouter(prefix="/condense", tags=["condense"])


class CondenseStatus(BaseModel):
    id: str
    status: str  # queued | scanning | rendering | completed | empty | failed
    progress: float = 0.0
    source_name: str | None = None
    min_gap_seconds: int | None = None
    source_duration_ms: int | None = None
    condensed_duration_ms: int | None = None
    cut_ms: int | None = None
    segment_count: int | None = None
    segments: list[list[int]] | None = None


def _rq_id(condense_id: str) -> str:
    return f"condense-{condense_id}"


def _base_dir(settings, condense_id: str) -> Path:
    return settings.storage_root / "condense" / condense_id


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.is_file() else {}


@router.post("", response_model=CondenseStatus, status_code=status.HTTP_201_CREATED)
async def create_condense(
    request: Request,
    filename_header: Annotated[str, Header(alias="X-Filename")],
    min_gap_seconds: int = 15,
) -> CondenseStatus:
    settings = get_settings()
    filename = _safe_filename(unquote(filename_header))
    if Path(filename).suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Unsupported video format")
    min_gap_seconds = max(1, min(120, min_gap_seconds))

    condense_id = str(uuid.uuid4())
    base = _base_dir(settings, condense_id)
    base.mkdir(parents=True, exist_ok=False)
    source = base / "source.mp4"
    partial = base / "source.mp4.part"
    size = 0
    try:
        with partial.open("xb") as destination:
            async for chunk in request.stream():
                if not chunk:
                    continue
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Video exceeds 2 GB"
                    )
                destination.write(chunk)
        partial.replace(source)
        PyAVVideoReader().probe(source)
    except HTTPException:
        shutil.rmtree(base, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(base, ignore_errors=True)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid video: {exc}") from exc

    (base / "upload.json").write_text(
        json.dumps(
            {
                "source_name": filename,
                "min_gap_seconds": min_gap_seconds,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
    )
    queue = Queue(settings.rq_queue, connection=Redis.from_url(settings.redis_url))
    queue.enqueue(
        "app.workers.condense.condense_video",
        condense_id,
        filename,
        min_gap_seconds=min_gap_seconds,
        job_id=_rq_id(condense_id),
        job_timeout="1h",
        result_ttl=86400,
        failure_ttl=604800,
    )
    return CondenseStatus(
        id=condense_id, status="queued", source_name=filename, min_gap_seconds=min_gap_seconds
    )


@router.get("", response_model=list[CondenseStatus])
def list_condense() -> list[CondenseStatus]:
    settings = get_settings()
    root = settings.storage_root / "condense"
    if not root.is_dir():
        return []
    items: list[CondenseStatus] = []
    for base in sorted(root.iterdir(), key=lambda path: path.name, reverse=True):
        if not base.is_dir():
            continue
        result = _read_json(base / "result.json")
        if result:
            items.append(
                CondenseStatus(
                    id=base.name,
                    status=result["status"],
                    progress=1.0,
                    source_name=result.get("source_name"),
                    min_gap_seconds=result.get("min_gap_seconds"),
                    source_duration_ms=result.get("source_duration_ms"),
                    condensed_duration_ms=result.get("condensed_duration_ms"),
                    cut_ms=result.get("cut_ms"),
                    segment_count=result.get("segment_count"),
                )
            )
            continue
        upload = _read_json(base / "upload.json")
        if upload:
            items.append(
                CondenseStatus(
                    id=base.name,
                    status="processing",
                    source_name=upload.get("source_name"),
                    min_gap_seconds=upload.get("min_gap_seconds"),
                )
            )
    return items


@router.get("/{condense_id}", response_model=CondenseStatus)
def get_condense(condense_id: str) -> CondenseStatus:
    settings = get_settings()
    base = _base_dir(settings, condense_id)
    if not base.is_dir():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Condense task not found")

    result_path = base / "result.json"
    if result_path.is_file():
        data = json.loads(result_path.read_text())
        return CondenseStatus(
            id=condense_id,
            status=data["status"],
            progress=1.0,
            source_name=data.get("source_name"),
            min_gap_seconds=data.get("min_gap_seconds"),
            source_duration_ms=data.get("source_duration_ms"),
            condensed_duration_ms=data.get("condensed_duration_ms"),
            cut_ms=data.get("cut_ms"),
            segment_count=data.get("segment_count"),
            segments=data.get("segments"),
        )

    upload = _read_json(base / "upload.json")
    try:
        rq_job = RqJob.fetch(_rq_id(condense_id), connection=Redis.from_url(settings.redis_url))
        rq_status = rq_job.get_status(refresh=True)
        stage = rq_job.meta.get("stage", "processing")
        progress = float(rq_job.meta.get("progress", 0.0))
    except NoSuchJobError:
        rq_status, stage, progress = "unknown", "queued", 0.0

    status_map = {
        "queued": "queued",
        "deferred": "queued",
        "scheduled": "queued",
        "started": stage,
        "failed": "failed",
    }
    return CondenseStatus(
        id=condense_id,
        status=status_map.get(rq_status, "queued"),
        progress=progress,
        source_name=upload.get("source_name"),
        min_gap_seconds=upload.get("min_gap_seconds"),
    )


@router.get("/{condense_id}/download", response_class=FileResponse)
def download_condensed(condense_id: str) -> FileResponse:
    settings = get_settings()
    base = _base_dir(settings, condense_id)
    output = base / "condensed.mp4"
    if not output.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Condensed video not ready")
    upload = _read_json(base / "upload.json")
    stem = Path(upload.get("source_name", "video.mp4")).stem
    return FileResponse(output, media_type="video/mp4", filename=f"condensed_{stem}.mp4")


@router.post("/{condense_id}/to-job", status_code=status.HTTP_201_CREATED)
def send_to_job(
    condense_id: str, session: Annotated[Session, Depends(get_db)]
) -> dict[str, str]:
    """Feed the condensed video into the normal ALPR pipeline as a new processing job."""

    settings = get_settings()
    base = _base_dir(settings, condense_id)
    output = base / "condensed.mp4"
    if not output.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Condensed video not ready")
    upload = _read_json(base / "upload.json")
    source_stem = Path(upload.get("source_name", "video.mp4")).stem
    filename = _safe_filename(f"condensed_{source_stem}.mp4")

    upload_id = uuid.uuid4()
    upload_dir = settings.storage_root / "uploads" / str(upload_id)
    upload_dir.mkdir(parents=True, exist_ok=False)
    final_path = upload_dir / filename
    shutil.copyfile(output, final_path)
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
        sample_rate=4.0,
        status="QUEUED",
        current_stage="QUEUED",
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
            "condensed_from": condense_id,
        },
    )
    session.add(job)
    session.flush()
    session.add(
        JobEvent(
            job_id=job.id,
            event_type="VIDEO_UPLOADED",
            message="Condensed video queued for plate recognition",
            data={"condensed_from": condense_id},
        )
    )
    queue = Queue(settings.rq_queue, connection=Redis.from_url(settings.redis_url))
    queue.enqueue(
        "app.workers.pipeline.process_plate_job",
        str(job.id),
        job_id=str(job.id),
        result_ttl=86400,
        failure_ttl=604800,
        job_timeout="6h",
        on_failure=mark_job_failed,
    )
    session.commit()
    return {"job_id": str(job.id)}
