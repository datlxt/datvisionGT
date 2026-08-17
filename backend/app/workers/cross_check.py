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
    CloudReading,
    quality_group,
    read_plate_openai,
    read_plate_qwen,
    read_plate_third,
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
    work: list[tuple[Track, Detection, RecognitionResult | None, bytes, float]] = []
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
        work.append(
            (track, vehicle, recognition, image_bytes, plate.detection_confidence or 0.0)
        )

    # Phase 2 — call the cloud readers for ALL plates CONCURRENTLY. This is pure network I/O
    # (the slow part), so a thread pool turns N sequential round-trips into N/CONCURRENCY — the
    # whole cross-check goes from minutes to seconds. No DB access happens inside the threads.
    def read_pair(item: tuple[Track, Detection, RecognitionResult | None, bytes]):
        img = item[3]
        return read_plate_openai(img, settings), read_plate_qwen(img, settings)

    def quality_split(gpt: CloudReading | None, qwen: CloudReading | None) -> bool:
        """True when AI-1 & AI-2 do NOT already form a settled classification (≥2 labels agreeing).
        Only then is the AI-3 tie-breaker worth a call."""

        groups = [quality_group(r.quality) for r in (gpt, qwen) if r and r.quality]
        groups = [g for g in groups if g]
        return not (len(groups) >= 2 and len(set(groups)) == 1)

    ai_reads: list[tuple[CloudReading | None, CloudReading | None, CloudReading | None]] = []
    if work:
        with ThreadPoolExecutor(max_workers=_CROSS_CHECK_CONCURRENCY) as pool:
            # Phase 2a — AI-1 + AI-2 for EVERY crop (they vote on both reading and classification).
            pair_reads = list(pool.map(read_pair, work))
            # Phase 2b — AI-3 (the classification tie-breaker) ONLY where AI-1 & AI-2 split on the
            # quality group. Firing on the minority of ties keeps a free-tier third vendor well under
            # its daily quota and adds no latency to the agreeing majority.
            tiebreak_idx = [
                i for i, (gpt, qwen) in enumerate(pair_reads) if quality_split(gpt, qwen)
            ]
            third_by_idx: dict[int, CloudReading | None] = {}
            if tiebreak_idx:
                thirds = list(
                    pool.map(lambda img: read_plate_third(img, settings),
                             [work[i][3] for i in tiebreak_idx])
                )
                third_by_idx = dict(zip(tiebreak_idx, thirds, strict=True))
        ai_reads = [
            (gpt, qwen, third_by_idx.get(i)) for i, (gpt, qwen) in enumerate(pair_reads)
        ]

    # Phase 3 — apply results + write flags (sequential, main thread — DB is not thread-safe).
    for (track, vehicle, recognition, _image, plate_conf), (gpt, qwen, third) in zip(
        work, ai_reads, strict=True
    ):
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
            # AI-3 is a CLASSIFICATION-only tie-breaker — its quality label votes, its plate read
            # does NOT (reading already has 3 sources: local OCR + AI-1 + AI-2).
            raw["third_quality"] = third.quality if third else None
            # (1) OCR cross-check by MAJORITY vote — local CCT + AI-1 + AI-2 (3 sources; the local
            # OCR is the third reader, so NO cloud reader is added here). A single weak reader
            # shouldn't derail a plate the others agree on, so we trust the majority and only flag
            # when they are genuinely split — including a reader that sees no plate where others
            # read one (detection error).
            keys = []
            if recognition:
                keys.append(plate_key(recognition.predicted_text))
            for reader in (gpt, qwen):
                if reader is not None:
                    keys.append(plate_key(reader.plate) if reader.plate else "")
            present = [k for k in keys if k]
            top_count = Counter(present).most_common(1)[0][1] if present else 0
            # UNANIMOUS = every reader that gave an answer read the SAME plate. A mere majority is
            # not enough on a hard plate — glare/occlusion can make two readers land on the same
            # WRONG plate while another dissents. So a dissenting reader keeps the case flagged;
            # only full agreement clears the doubt.
            unanimous = len(present) >= 2 and len(set(present)) == 1
            if top_count >= 2 and 2 * top_count > len(keys):
                flags.append("OCR_AGREE")  # a clear majority read the same plate
                if unanimous:
                    flags.append("OCR_UNANIMOUS")  # every reader agreed — strongest confidence
                agree += 1
            else:
                flags.append("OCR_DISAGREEMENT")  # no majority — a human decides
                disagree += 1
            # A crop the local model read a string on but that the cloud readers can't confirm is
            # almost certainly NOT a plate (a logo / decal / tail-light the detector fired on) — real
            # plates get read by the AIs too. Two triggers, both requiring the local read to exist:
            #   (a) BOTH reading AIs saw NOTHING there — the classic false positive, OR
            #   (b) at least ONE AI saw nothing, NONE backs the local string, and it was read from a
            #       SINGLE frame — e.g. a YADEA logo the OCR turned into "19G20011" once while AI-1
            #       reads empty and AI-2 hallucinates unrelated garbage. A real plate is read across
            #       many frames and at least one AI lands on it.
            ai_present = [r for r in (gpt, qwen) if r is not None]
            if recognition is not None and recognition.predicted_text and ai_present:
                local_key = plate_key(recognition.predicted_text)
                ai_keys = [plate_key(r.plate) for r in ai_present if r.plate]
                any_ai_empty = any(not r.plate for r in ai_present)
                no_ai_backs_local = local_key not in ai_keys
                both_ai_empty = len(ai_present) >= 2 and not ai_keys
                # The crop looks non-plate-ish on its own: read from a SINGLE frame, OR the plate
                # DETECTOR itself was not confident it is a plate (a logo/decal fires with a low
                # score). Either — combined with the readers failing to confirm — marks it a false
                # plate. The detector-confidence arm also catches a STABLE logo read across many
                # frames, not just a one-frame blip.
                weak_shape = "SINGLE_READING_OCR" in flags or plate_conf < 0.50
                if both_ai_empty or (any_ai_empty and no_ai_backs_local and weak_shape):
                    flags.append("SUSPECTED_NON_PLATE")
            # (2) Quality classification by 2/3 MAJORITY across the THREE AIs (the local model has
            # no quality label, so only the AIs vote). Readers are grouped into coarse categories
            # ("bẩn"/"cũ,xước,mờ" → degrade, "che"/"dán" → occlusion…) so trivially different fine
            # labels in the SAME group still count as agreement. ≥2 in one group → take it (the
            # majority's fine label is pre-filled, a light hint); no majority (each AI a different
            # group, e.g. degrade vs glare vs occlusion) → flag it, and the reviewer picks. This is
            # exactly the "bị che vật lý" case: two AIs guessing different cosmetic issues while a
            # third sees occlusion is a 3-way split → it lands in "Cần xem lại".
            ai_quality = [
                (reader.quality, quality_group(reader.quality))
                for reader in (gpt, qwen, third)
                if reader is not None and reader.quality
            ]
            raw["cloud_quality_all"] = [q for q, _ in ai_quality]
            groups = [g for _, g in ai_quality if g]
            if groups:
                top_group, top_n = Counter(groups).most_common(1)[0]
                if top_n >= 2:
                    flags.append("QUALITY_AGREE")
                    # Pre-fill the fine label from the FIRST reader in the winning group.
                    raw["cloud_quality"] = next(
                        q for q, g in ai_quality if g == top_group
                    )
                else:
                    flags.append("QUALITY_DISAGREEMENT")  # 1-1-1 split — a human decides
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
