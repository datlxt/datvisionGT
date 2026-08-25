"""Missed-vehicle recall (soát bỏ sót) — an INDEPENDENT background safety-net.

It runs on its OWN background task (``missed_scan_job``), enqueued right after a job reaches review —
NOT folded into the AI cross-check — so the "nghi bỏ sót" bar fills on its own without the reviewer
waiting on (or even running) the plate cross-check.

The offline pipeline only emits a vehicle event when its detector + tracker are confident. This
step does the OPPOSITE check: for every long detection GAP the pipeline left empty, it crops the
lane (ROI) from a few sampled full frames and asks ONE cloud AI "is there a vehicle here, and if so
read any plate?".

- AI says YES  → keep the gap on the reviewer's "nghi bỏ sót" timeline (a possible missed pass),
  attaching the frame the AI saw + the plate it read + whether that moment is already in the list.
- AI says NO   → drop the gap; the road was genuinely empty, so the QC never wastes a look on it.

It NEVER writes GT, a plate, or an event — it only annotates the job so the QC's attention is
spent where it matters. Fully decoupled from the offline pipeline: every failure is swallowed and
simply leaves the gaps un-scanned (they fall back to plain time-gaps on the timeline).
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

import cv2
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.models import Artifact, ProcessingJob, Track
from app.models.recognition import RecognitionResult
from app.vision.plate.cloud_ocr import read_gap_vehicle_openai
from app.vision.plate.domain import plate_key

# A gap must be at least this long to be worth scanning — matches the frontend "nghi bỏ sót" bar.
# Lowered 6s → 3.5s to also scan SHORT gaps (e.g. a bicycle briefly crossing) at the cost of more AI
# calls + more candidates for the human to confirm.
_MIN_GAP_MS = 2_000
# Each detected vehicle interval is padded by this on EACH side before computing gaps, so a car's
# lead-in / standstill overhang (its track can end while it still idles at the barrier) is treated as
# "covered". Tuned to OVER-WARN (a missed vehicle is far worse than a spare warning the human dismisses
# in one click): 4s → a real gap must exceed ~14s (was 18s) so more borderline stretches get scanned.
_OVERHANG_PAD_MS = 1_200
# Ignore this much at each gap edge. Kept small so a vehicle passing near a gap boundary (e.g. a slow
# scooter being WALKED through) is still sampled instead of inset away.
_EDGE_MARGIN_MS = 1_000
# If the AI reads a plate in a gap that matches a detected vehicle within this window, it is the SAME
# pass (a car idling past its track's end), not a miss — so the gap is dropped.
_SAME_PASS_WINDOW_MS = 90_000
# A sighting within this many ms of a detected vehicle's edge is that car's lead-in / standstill
# overhang (its track can end while it still idles at the barrier), NOT a miss — so it's dropped.
# This is the main filter, since the AI usually can't read the small/far plate in a wide gap frame.
_ADJACENT_VEHICLE_MS = 6_000
# Sampled frames probed per gap (evenly spaced). Raised so a vehicle that only shows for a moment
# inside a gap (or near its edge) isn't skipped between samples. One YES keeps the gap.
_SAMPLES_PER_GAP = 9
# Hard cap on AI calls per job so a pathological (long, sparse) video can't run up cost / time.
_MAX_PROBES = 150
_RECALL_CONCURRENCY = 6


def set_missed_status(session: Session, job: ProcessingJob, status: dict) -> None:
    """Record the missed-scan result on the job so the review UI can show the refined timeline."""

    snapshot = dict(job.config_snapshot or {})
    snapshot["missed_scan"] = status
    job.config_snapshot = snapshot
    flag_modified(job, "config_snapshot")
    session.commit()


def _roi_norm(job: ProcessingJob) -> tuple[float, float, float, float] | None:
    roi = (job.config_snapshot or {}).get("roi")
    if isinstance(roi, list) and len(roi) == 4:
        try:
            x1, y1, x2, y2 = (float(v) for v in roi)
            if x2 > x1 and y2 > y1:
                return x1, y1, x2, y2
        except (TypeError, ValueError):
            return None
    return None


def _detected_intervals(session: Session, job: ProcessingJob) -> list[tuple[int, int]]:
    """Every vehicle pass SHOWN ON THE TIMELINE as a [start, end] ms interval, merged where they
    overlap.

    Uses the SAME consolidated events the reviewer sees (``_load_results``), NOT raw VEHICLE tracks.
    This is the whole point of the safety-net: the AI must scan exactly the empty stretches the
    reviewer sees on the timeline ("rà trên đoạn mà dòng line hiện"). A track that was DROPPED from
    the displayed list — e.g. a 2-minute motion blob tracked across the whole video, or a no-plate
    fragment overlapping a recognized pass — must NOT count as "covered" here. Counting raw tracks let
    one such garbage track blanket the timeline so the recall reported "0 gaps" while the reviewer
    plainly saw empty stretches. Conversely, a real no-plate / bicycle that the pipeline dropped now
    leaves a genuine gap here → the AI scans it → it surfaces as "nghi bỏ sót" for the human.
    """

    # Local import avoids an api<->worker import cycle at module load.
    from app.api.results import _load_results

    _job, events = _load_results(job.id, session)
    intervals = sorted(
        (int(event.start_timestamp_ms), int(event.end_timestamp_ms)) for event in events
    )
    merged: list[tuple[int, int]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _detected_plates(session: Session, job: ProcessingJob) -> list[tuple[str, int, int]]:
    """Every detected plate as (plate_key, start_ms, end_ms) — used to tell the reviewer whether a
    plate the recall AI read is ALREADY in the list (and, if elsewhere, at what time)."""

    rows = session.execute(
        select(
            RecognitionResult.predicted_text,
            Track.start_timestamp_ms,
            Track.end_timestamp_ms,
        )
        .join(Track, Track.id == RecognitionResult.track_id)
        .where(
            Track.job_id == job.id,
            RecognitionResult.stage == "TRACK_VOTE",
            RecognitionResult.predicted_text.is_not(None),
        )
    ).all()
    out: list[tuple[str, int, int]] = []
    for text, start, end in rows:
        key = plate_key(text or "")
        if key:
            out.append((key, int(start), int(end)))
    return out


def _gaps(intervals: list[tuple[int, int]], duration_ms: int) -> list[tuple[int, int]]:
    """Empty stretches (no detected vehicle) worth flagging as a possible miss.

    Each detected interval is PADDED by ``_OVERHANG_PAD_MS`` first: a car's lead-in and its standstill
    overhang (its track can end while it still idles at the barrier) count as "covered", so the brief
    empty stretch right next to a detected car is NOT wrongly flagged. Only a stretch that stays empty
    AFTER padding — genuinely far from any detected car, i.e. long enough to hide a real missed pass —
    becomes a candidate. Includes the lead-in before the first vehicle and the tail after the last.
    """

    padded: list[tuple[int, int]] = []
    for start, end in intervals:
        s, e = start - _OVERHANG_PAD_MS, end + _OVERHANG_PAD_MS
        if padded and s <= padded[-1][1]:
            padded[-1] = (padded[-1][0], max(padded[-1][1], e))
        else:
            padded.append((s, e))
    gaps: list[tuple[int, int]] = []
    prev_end = 0
    for start, end in padded:
        if start - prev_end >= _MIN_GAP_MS:
            gaps.append((max(0, prev_end), start))
        prev_end = max(prev_end, end)
    if duration_ms and duration_ms - prev_end >= _MIN_GAP_MS:
        gaps.append((prev_end, duration_ms))
    return gaps


def _evenly(items: list, count: int) -> list:
    """Pick up to ``count`` evenly-spaced items (keeps the sampling spread across the gap)."""

    if len(items) <= count:
        return items
    step = (len(items) - 1) / (count - 1)
    return [items[round(i * step)] for i in range(count)]


def _load_roi_jpeg(path, roi: tuple[float, float, float, float] | None) -> bytes | None:
    """Read a full frame, crop to the ROI (the lane), and re-encode as JPEG for the AI."""

    try:
        img = cv2.imread(str(path))
        if img is None:
            return None
        height, width = img.shape[:2]
        if roi is not None:
            x1 = max(0, int(roi[0] * width))
            y1 = max(0, int(roi[1] * height))
            x2 = min(width, int(roi[2] * width))
            y2 = min(height, int(roi[3] * height))
            if x2 - x1 >= 8 and y2 - y1 >= 8:
                img = img[y1:y2, x1:x2]
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        return buf.tobytes() if ok else None
    except Exception:
        return None


def scan_missed_vehicles(session: Session, job: ProcessingJob, settings: Settings) -> dict:
    """Probe every detection gap of ``job`` with the recall AI; return the confirmed misses.

    Result shape (stored under ``config_snapshot["missed_scan"]``)::

        {"status": "done", "gaps": N, "scanned": M,
         "candidates": [{"start_ms", "end_ms", "ts_ms", "frame_url"}, ...]}
    """

    roi = _roi_norm(job)
    intervals = _detected_intervals(session, job)
    detected_plates = _detected_plates(session, job)
    duration = int(job.duration_ms or 0)
    gaps = _gaps(intervals, duration)
    if not gaps:
        return {"status": "done", "gaps": 0, "scanned": 0, "candidates": []}

    frames = session.scalars(
        select(Artifact)
        .where(
            Artifact.job_id == job.id,
            Artifact.kind == "FULL_FRAME",
            Artifact.timestamp_ms.is_not(None),
        )
        .order_by(Artifact.timestamp_ms)
    ).all()

    # Phase 1 — gather ROI crops to probe (main thread; no DB access inside the threads below).
    probes: list[tuple[int, int, str, bytes]] = []  # (gap_idx, ts_ms, storage_key, jpeg)
    for gap_idx, (g0, g1) in enumerate(gaps):
        # Inset the gap edges: a vehicle detected right up to g0 (or from g1) is often still leaving
        # / entering frame in the first/last second of the gap, so sampling the very edge re-flags an
        # already-counted pass. Probe the MIDDLE of the gap instead (never insets away the whole gap).
        margin = min(_EDGE_MARGIN_MS, int((g1 - g0) * 0.34))
        lo, hi = g0 + margin, g1 - margin
        in_gap = [a for a in frames if lo <= (a.timestamp_ms or -1) <= hi]
        for art in _evenly(in_gap, _SAMPLES_PER_GAP):
            if len(probes) >= _MAX_PROBES:
                break
            crop = _load_roi_jpeg(settings.storage_root / art.storage_key, roi)
            if crop is not None:
                probes.append((gap_idx, int(art.timestamp_ms), art.storage_key, crop))
    if not probes:
        return {"status": "done", "gaps": len(gaps), "scanned": 0, "candidates": []}

    # Phase 2 — ask the AI for every probe CONCURRENTLY (pure network I/O). The AI reports whether a
    # vehicle is present AND, if so, reads any visible plate.
    with ThreadPoolExecutor(max_workers=_RECALL_CONCURRENCY) as pool:
        readings = list(pool.map(lambda item: read_gap_vehicle_openai(item[3], settings), probes))

    def _covered(ts: int) -> bool:
        # Is this exact moment already a detected event? (By timestamp — a SAME plate at a DIFFERENT
        # time is a different pass and does NOT count as "already in the list".)
        return any(start <= ts <= end for start, end in intervals)

    def _near_detected(ts: int) -> bool:
        # True if ts is within _ADJACENT_VEHICLE_MS of a detected vehicle's interval — i.e. the
        # lead-in or standstill overhang of an already-detected car, not a genuine missed pass.
        return any(
            start - _ADJACENT_VEHICLE_MS <= ts <= end + _ADJACENT_VEHICLE_MS
            for start, end in intervals
        )

    def _plate_elsewhere(plate: str, ts: int) -> int | None:
        # If the AI read a plate, is that plate in the list at ANOTHER time? Return the nearest such
        # time (info only — helps the reviewer see it's a re-entry of a known plate, not this moment).
        key = plate_key(plate)
        if not key:
            return None
        centers = [
            (start + end) // 2 for pkey, start, end in detected_plates if pkey == key
        ]
        return min(centers, key=lambda c: abs(c - ts)) if centers else None

    # Phase 3 — first frame with a vehicle per gap becomes a confirmed missed-vehicle candidate.
    confirmed: dict[int, dict] = {}
    for (gap_idx, ts, key, _crop), reading in zip(probes, readings, strict=True):
        if reading and reading.get("vehicle") and gap_idx not in confirmed:
            # Drop the lead-in / standstill overhang of an already-detected car: a car that idles at
            # the barrier stays in frame PAST its track's end, so the gap right after keeps showing it
            # (e.g. 21D00519: track ends 10:26, still parked at 10:28). A real miss is a vehicle deep
            # in an empty stretch, far from any detected car. This filter works even when the AI can't
            # read the plate from the wide gap frame (which is the usual case).
            if _near_detected(ts):
                continue
            filename = key.rsplit("/", 1)[-1]
            plate = reading.get("plate") or ""
            nearest_same_plate = _plate_elsewhere(plate, ts) if plate else None
            # Also drop if a READ plate matches a detected vehicle within one pass-window (±90s) —
            # belt-and-suspenders for the rare case the AI does read the overhang car's plate.
            if (
                nearest_same_plate is not None
                and abs(nearest_same_plate - ts) <= _SAME_PASS_WINDOW_MS
            ):
                continue
            confirmed[gap_idx] = {
                "start_ms": gaps[gap_idx][0],
                "end_ms": gaps[gap_idx][1],
                "ts_ms": ts,
                "frame_url": f"/api/v1/evidence/{job.id}/frames/{filename}",
                "vehicle": True,
                "has_plate": bool(plate),
                "plate": plate,
                "vehicle_type": reading.get("vehicle_type") or "",
                "in_list": _covered(ts),
                "plate_elsewhere_ms": nearest_same_plate,
            }
    candidates = [confirmed[i] for i in sorted(confirmed)]
    return {
        "status": "done",
        "gaps": len(gaps),
        "scanned": len(probes),
        "candidates": candidates,
    }


def missed_scan_job(job_id: str) -> dict:
    """RQ task: run the missed-vehicle recall for a job IN THE BACKGROUND, independent of the AI
    cross-check. Enqueued right after the job reaches review so the "nghi bỏ sót" bar fills on its
    own. Never raises — a failure just marks the scan errored."""

    settings = get_settings()
    if not settings.cloud_ocr_available:
        return {"skipped": "cloud_ocr_unavailable"}
    with SessionLocal() as session:
        job = session.get(ProcessingJob, uuid.UUID(str(job_id)))
        if job is None:
            return {"skipped": "job_not_found"}
        try:
            set_missed_status(session, job, {"status": "running", "candidates": []})
            result = scan_missed_vehicles(session, job, settings)
            set_missed_status(session, job, result)
            return {"candidates": len(result.get("candidates", []))}
        except Exception:
            session.rollback()
            set_missed_status(session, job, {"status": "error", "candidates": []})
            return {"error": True}
