"""Missed-vehicle recall (soát bỏ sót) — a background SAFETY-NET run after cross-check.

The offline pipeline only emits a vehicle event when its detector + tracker are confident. This
step does the OPPOSITE check: for every long detection GAP the pipeline left empty, it crops the
lane (ROI) from a few sampled full frames and asks ONE cloud AI "is there a vehicle here?".

- AI says YES  → keep the gap on the reviewer's "nghi bỏ sót" timeline (a possible missed pass),
  attaching the frame the AI saw so the QC can confirm and add the case.
- AI says NO   → drop the gap; the road was genuinely empty, so the QC never wastes a look on it.

It NEVER writes GT, a plate, or an event — it only annotates the job so the QC's attention is
spent where it matters. Fully decoupled from the offline pipeline: every failure is swallowed and
simply leaves the gaps un-scanned (they fall back to plain time-gaps on the timeline).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import cv2
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import Settings
from app.models import Artifact, ProcessingJob, Track
from app.vision.plate.cloud_ocr import check_vehicle_openai

# A gap must be at least this long to be worth scanning — matches the frontend "nghi bỏ sót" bar.
_MIN_GAP_MS = 6_000
# Ignore this much at each gap edge: a vehicle detected right up to the boundary is often still
# leaving/entering frame there, so sampling the edge would re-flag an already-counted pass.
_EDGE_MARGIN_MS = 2_000
# Sampled frames probed per gap (evenly spaced across the gap). One YES is enough to keep the gap.
_SAMPLES_PER_GAP = 3
# Hard cap on AI calls per job so a pathological (long, sparse) video can't run up cost / time.
_MAX_PROBES = 90
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
    """Every detected vehicle pass as a [start, end] ms interval, merged where they overlap.

    Uses raw VEHICLE tracks (pre-consolidation fragments included) — more intervals means smaller
    gaps, so we never invent a gap where the pipeline actually saw a vehicle.
    """

    rows = session.execute(
        select(Track.start_timestamp_ms, Track.end_timestamp_ms).where(
            Track.job_id == job.id, Track.object_type == "VEHICLE"
        )
    ).all()
    intervals = sorted((int(a), int(b)) for a, b in rows)
    merged: list[tuple[int, int]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _gaps(intervals: list[tuple[int, int]], duration_ms: int) -> list[tuple[int, int]]:
    """Empty stretches (no detected vehicle) at least ``_MIN_GAP_MS`` long, including the lead-in
    before the first vehicle and the tail after the last."""

    gaps: list[tuple[int, int]] = []
    prev_end = 0
    for start, end in intervals:
        if start - prev_end >= _MIN_GAP_MS:
            gaps.append((prev_end, start))
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

    # Phase 2 — ask the AI for every probe CONCURRENTLY (pure network I/O).
    with ThreadPoolExecutor(max_workers=_RECALL_CONCURRENCY) as pool:
        verdicts = list(pool.map(lambda item: check_vehicle_openai(item[3], settings), probes))

    # Phase 3 — first YES per gap becomes a confirmed missed-vehicle candidate.
    confirmed: dict[int, dict] = {}
    for (gap_idx, ts, key, _crop), verdict in zip(probes, verdicts, strict=True):
        if verdict and gap_idx not in confirmed:
            filename = key.rsplit("/", 1)[-1]
            confirmed[gap_idx] = {
                "start_ms": gaps[gap_idx][0],
                "end_ms": gaps[gap_idx][1],
                "ts_ms": ts,
                "frame_url": f"/api/v1/evidence/{job.id}/frames/{filename}",
            }
    candidates = [confirmed[i] for i in sorted(confirmed)]
    return {
        "status": "done",
        "gaps": len(gaps),
        "scanned": len(probes),
        "candidates": candidates,
    }
