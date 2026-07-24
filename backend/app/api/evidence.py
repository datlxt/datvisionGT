import mimetypes
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.config import get_settings

router = APIRouter(prefix="/evidence", tags=["evidence"])
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$")


def _safe_file(root: Path, *parts: str) -> Path:
    if any(not SAFE_NAME.fullmatch(part) for part in parts):
        raise HTTPException(status_code=400, detail="Invalid evidence path")
    resolved_root = root.resolve()
    candidate = resolved_root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(resolved_root) or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Evidence file not found")
    return candidate


@router.get("/{job_id}/frames/{filename}")
def evidence_frame(job_id: str, filename: str) -> FileResponse:
    settings = get_settings()
    path = _safe_file(settings.storage_root / "jobs", job_id, "frames", filename)
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise HTTPException(status_code=400, detail="Unsupported evidence type")
    return FileResponse(
        path,
        media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=60"},
    )


@router.get("/{job_id}/crops/{filename}")
def evidence_crop(job_id: str, filename: str) -> FileResponse:
    settings = get_settings()
    path = _safe_file(settings.storage_root / "jobs", job_id, "crops", filename)
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise HTTPException(status_code=400, detail="Unsupported evidence type")
    return FileResponse(
        path,
        media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=60"},
    )


@router.get("/videos/{filename}")
def source_video(filename: str) -> FileResponse:
    settings = get_settings()
    path = _safe_file(settings.storage_root / "uploads", filename)
    if path.suffix.lower() not in {".mp4", ".mov", ".mkv", ".avi"}:
        raise HTTPException(status_code=400, detail="Unsupported video type")
    return FileResponse(
        path,
        media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
    )
