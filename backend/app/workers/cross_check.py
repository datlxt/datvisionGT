"""Cloud OCR cross-check — reusable service + background RQ task.

A SECOND (and third) INDEPENDENT reader re-reads every plate crop; the result is compared with
the local model. Agreement raises trust, disagreement flags the case for a human. This lives
OUTSIDE the offline video pipeline: it runs either on demand (the review "Kiểm chéo AI" button)
or is enqueued automatically after processing when cloud readers are configured — so the core
pipeline stays offline and deterministic, and the reviewer opens a job with the AI opinions ready.
"""

from __future__ import annotations

import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.models import Artifact, Detection, ProcessingJob, Track
from app.models.recognition import RecognitionResult
from app.vision.plate.cloud_ocr import (
    quality_group,
    read_plate_openai,
    read_plate_qwen,
)
from app.vision.plate.domain import plate_key

# Plates read in parallel per run. The bottleneck is per-plate network latency, so concurrency
# collapses the total wait; kept moderate to stay under typical cloud rate limits.
_CROSS_CHECK_CONCURRENCY = 8

# Flags this pass owns — cleared before each run so re-running is idempotent.
CROSS_CHECK_FLAGS = (
    "OCR_AGREE",
    "OCR_UNANIMOUS",
    "OCR_DISAGREEMENT",
    "OCR_UNVERIFIED",
    "QUALITY_AGREE",
    "QUALITY_DISAGREEMENT",
    "SUSPECTED_NON_PLATE",
)


def set_cross_check_status(session: Session, job: ProcessingJob, status: dict) -> None:
    """Record cross-check progress on the job so the review UI can show it and auto-refresh."""

    snapshot = dict(job.config_snapshot or {})
    snapshot["cross_check"] = status
    job.config_snapshot = snapshot
    flag_modified(job, "config_snapshot")
    session.commit()


def run_cross_check(
    session: Session, job: ProcessingJob, settings: Settings
) -> tuple[int, int, int]:
    """Re-read every plate of ``job`` with the cloud reader(s); persist agreement flags.

    Returns ``(agree, disagree, unverified)``. Commits the session.
    """

    set_cross_check_status(session, job, {"status": "running"})
    agree = disagree = unverified = 0

    # Phase 1 — gather every plate crop + its context (fast DB reads, main thread only).
    work: list[tuple[Track, Detection, RecognitionResult | None, bytes]] = []
    tracks = session.scalars(
        select(Track).where(Track.job_id == job.id, Track.object_type == "VEHICLE")
    ).all()
    for track in tracks:
        detections = list(
            session.scalars(select(Detection).where(Detection.track_id == track.id)).all()
        )
        vehicle = next((d for d in detections if d.object_type == "VEHICLE"), None)
        plate = next((d for d in detections if d.object_type == "PLATE"), None)
        if vehicle is None or plate is None or plate.crop_artifact_id is None:
            continue
        crop_art = session.get(Artifact, plate.crop_artifact_id)
        if crop_art is None:
            continue
        try:
            image_bytes = (settings.storage_root / crop_art.storage_key).read_bytes()
        except OSError:
            continue
        recognition = session.scalar(
            select(RecognitionResult).where(
                RecognitionResult.track_id == track.id,
                RecognitionResult.stage == "TRACK_VOTE",
            )
        )
        work.append((track, vehicle, recognition, image_bytes))

    # Phase 2 — call the cloud readers for ALL plates CONCURRENTLY. This is pure network I/O
    # (the slow part), so a thread pool turns N sequential round-trips into N/CONCURRENCY — the
    # whole cross-check goes from minutes to seconds. No DB access happens inside the threads.
    def read_both(item: tuple[Track, Detection, RecognitionResult | None, bytes]):
        img = item[3]
        return read_plate_openai(img, settings), read_plate_qwen(img, settings)

    if work:
        with ThreadPoolExecutor(max_workers=_CROSS_CHECK_CONCURRENCY) as pool:
            ai_reads = list(pool.map(read_both, work))
    else:
        ai_reads = []

    # Phase 3 — apply results + write flags (sequential, main thread — DB is not thread-safe).
    for (track, vehicle, recognition, _image), (gpt, qwen) in zip(work, ai_reads, strict=True):
        raw = dict(vehicle.raw_output or {})
        flags = [f for f in raw.get("quality_flags", []) if f not in CROSS_CHECK_FLAGS]
        if gpt is None and qwen is None:
            flags.append("OCR_UNVERIFIED")
            unverified += 1
        else:
            raw["cloud_plate"] = gpt.plate if gpt else None
            raw["cloud_quality"] = gpt.quality if gpt else None
            raw["qwen_plate"] = qwen.plate if qwen else None
            raw["qwen_quality"] = qwen.quality if qwen else None
            # (1) OCR cross-check by MAJORITY vote across every reader (local CCT + AI-1 + AI-2).
            # A single weak reader shouldn't derail a plate the others agree on, so we trust the
            # majority (≥2 of 3) and only flag when the readers are genuinely split (no majority) —
            # which includes a reader that sees no plate where others read one (detection error).
            keys = []
            if recognition:
                keys.append(plate_key(recognition.predicted_text))
            for reader in (gpt, qwen):
                if reader is not None:
                    keys.append(plate_key(reader.plate) if reader.plate else "")
            present = [k for k in keys if k]
            top_count = Counter(present).most_common(1)[0][1] if present else 0
            # UNANIMOUS = every reader that gave an answer read the SAME plate. A mere 2/3 majority
            # is NOT enough on a hard plate — glare/occlusion can make two readers land on the same
            # WRONG plate while the third dissents (89M1... misread as 89H... by local+AI-2). So a
            # dissenting reader keeps the case flagged; only full agreement clears the doubt.
            unanimous = len(present) >= 2 and len(set(present)) == 1
            if top_count >= 2 and 2 * top_count > len(keys):
                flags.append("OCR_AGREE")  # a clear majority read the same plate
                if unanimous:
                    flags.append("OCR_UNANIMOUS")  # every reader agreed — strongest confidence
                agree += 1
            else:
                flags.append("OCR_DISAGREEMENT")  # no majority — a human decides
                disagree += 1
            # A crop the local model read a plate on but that BOTH AIs saw as empty is almost
            # certainly NOT a plate (a logo / tail-light / sticker the detector fired on) — real
            # plates get read by the AIs too. Flag it so the reviewer can quickly discard it.
            ai_present = [r for r in (gpt, qwen) if r is not None]
            if (
                len(ai_present) >= 2
                and all(not reader.plate for reader in ai_present)
                and recognition is not None
                and recognition.predicted_text
            ):
                flags.append("SUSPECTED_NON_PLATE")
            # (2) Quality cross-check. Only the two AIs actually classify the plate quality (the
            # local model has no quality label), so this is a two-reader check: both agree → take
            # it (auto-filled); they differ → flag it, and the reviewer confirms the pre-filled
            # AI-1 suggestion with one click.
            gpt_group = quality_group(gpt.quality) if gpt and gpt.quality else None
            qwen_group = quality_group(qwen.quality) if qwen and qwen.quality else None
            if gpt_group and qwen_group:
                flags.append(
                    "QUALITY_AGREE" if gpt_group == qwen_group else "QUALITY_DISAGREEMENT"
                )
            elif gpt_group or qwen_group:
                # Only one AI answered — trust its single label (nothing to disagree with).
                flags.append("QUALITY_AGREE")
                raw["cloud_quality"] = gpt_group or qwen_group
        raw["quality_flags"] = flags
        vehicle.raw_output = raw
        flag_modified(vehicle, "raw_output")
    set_cross_check_status(
        session,
        job,
        {
            "status": "done",
            "checked": agree + disagree + unverified,
            "agree": agree,
            "disagree": disagree,
            "unverified": unverified,
        },
    )
    return agree, disagree, unverified


def cross_check_job(job_id: str) -> dict[str, int | str]:
    """RQ task: run the cross-check for a finished job (enqueued automatically after processing)."""

    settings = get_settings()
    if not settings.cloud_ocr_available:
        return {"skipped": "cloud_ocr_unavailable"}
    with SessionLocal() as session:
        job = session.get(ProcessingJob, uuid.UUID(str(job_id)))
        if job is None:
            return {"skipped": "job_not_found"}
        agree, disagree, unverified = run_cross_check(session, job, settings)
        # Fast-track the unanimous (all 3 readers agree) cases automatically — the reviewer only
        # deals with disagreements. Lazy import avoids a module-load cycle with the API layer.
        from app.api.ground_truth import auto_verify_unanimous

        auto_verified = auto_verify_unanimous(job.id, session)
        set_cross_check_status(
            session,
            job,
            {
                "status": "done",
                "checked": agree + disagree + unverified,
                "agree": agree,
                "disagree": disagree,
                "unverified": unverified,
                "auto_verified": auto_verified,
            },
        )
    return {
        "agree": agree,
        "disagree": disagree,
        "unverified": unverified,
        "auto_verified": auto_verified,
    }
