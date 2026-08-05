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

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.models import Artifact, Detection, ProcessingJob, Track
from app.models.recognition import RecognitionResult
from app.vision.plate.cloud_ocr import (
    local_quality_groups,
    quality_group,
    read_plate_openai,
    read_plate_qwen,
)
from app.vision.plate.domain import plate_key

# Flags this pass owns — cleared before each run so re-running is idempotent.
CROSS_CHECK_FLAGS = (
    "OCR_AGREE",
    "OCR_UNANIMOUS",
    "OCR_DISAGREEMENT",
    "OCR_UNVERIFIED",
    "QUALITY_AGREE",
    "QUALITY_DISAGREEMENT",
)


def run_cross_check(
    session: Session, job: ProcessingJob, settings: Settings
) -> tuple[int, int, int]:
    """Re-read every plate of ``job`` with the cloud reader(s); persist agreement flags.

    Returns ``(agree, disagree, unverified)``. Commits the session.
    """

    agree = disagree = unverified = 0
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

        gpt = read_plate_openai(image_bytes, settings)
        qwen = read_plate_qwen(image_bytes, settings)
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
            # (2) Quality cross-check. Two DIFFERENT-vendor AI labels are the primary check; the
            # deterministic local signal is the fallback when only one AI answered.
            groups = {
                quality_group(r.quality)
                for r in (gpt, qwen)
                if r is not None and quality_group(r.quality)
            }
            if len(groups) >= 2:
                flags.append("QUALITY_DISAGREEMENT")
            elif groups:
                (cloud_group,) = groups
                # Only when EVERY reader agreed (unanimous) is the local per-character doubt truly
                # resolved — then don't let WEAK_CHARACTER force a false quality conflict. A 2/3
                # majority on a hard plate keeps the doubt.
                signal_flags = (
                    [f for f in flags if f != "WEAK_CHARACTER"]
                    if "OCR_UNANIMOUS" in flags
                    else flags
                )
                local_groups = local_quality_groups(
                    track.classification, signal_flags, vehicle.quality_score
                )
                flags.append(
                    "QUALITY_AGREE" if cloud_group in local_groups else "QUALITY_DISAGREEMENT"
                )
        raw["quality_flags"] = flags
        vehicle.raw_output = raw
        flag_modified(vehicle, "raw_output")
    session.commit()
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
    return {"agree": agree, "disagree": disagree, "unverified": unverified}
