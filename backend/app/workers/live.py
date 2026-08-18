"""Live processing preview.

The offline worker publishes a small annotated snapshot (a downscaled JPEG + detection box
coordinates) to a Redis key as it detects; the API streams that key to the browser over SSE and the
frontend draws the boxes on a ``<canvas>`` overlay. This is BEST-EFFORT and fully decoupled from the
pipeline: it is throttled (~5 fps), encodes a tiny preview (so it never competes with detection the
way the old server-rendered live did), and every failure is swallowed so it can never break a job.
"""

from __future__ import annotations

import base64
import json
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import cv2

# The heavy resize+JPEG-encode runs on a DEDICATED thread, not the detection thread — OpenCV releases
# the GIL, so it genuinely runs in parallel and adds almost no cost to processing. That lets us push
# a higher, smoother frame rate. A frame is DROPPED (never queued) if the encoder is still busy, so
# the stream stays "latest only" and never backs up.
_MIN_INTERVAL_S = 0.10  # ~10 fps target for the smoothing previews
_PREVIEW_WIDTH = 720  # sharper (still ~15-20KB/frame; encode is off the detection thread)
_JPEG_QUALITY = 82

# Per-process wall-clock of the last publish, keyed by job id (throttle state).
_last_emit: dict[str, float] = {}
# One background encoder; the latest in-flight encode task per job (to drop frames when busy).
_encoder = ThreadPoolExecutor(max_workers=1, thread_name_prefix="live-encode")
_inflight: dict[str, Future] = {}


def _busy(job_id: str) -> bool:
    task = _inflight.get(job_id)
    return task is not None and not task.done()


def live_key(job_id: str) -> str:
    return f"live:{job_id}"


def events_key(job_id: str) -> str:
    return f"live_events:{job_id}"


def make_redis(url: str) -> Any:
    import redis as redis_lib

    return redis_lib.Redis.from_url(url)


def publish_frame(
    rconn: Any,
    job_id: str,
    bgr: Any,
    observations: list[Any],
    progress: float,
    roi: list[float] | tuple[float, ...] | None = None,
) -> None:
    """Queue a detected frame (WITH boxes) for the background encoder. Box frames arrive at the
    sample rate (~2 fps) and must always show, so they're submitted even if the encoder is busy."""

    try:
        _last_emit[job_id] = time.monotonic()  # gate intermediate previews after a real box frame
        _inflight[job_id] = _encoder.submit(
            _encode_frame, rconn, job_id, bgr, list(observations), progress, roi
        )
    except Exception:
        pass


def _encode_frame(
    rconn: Any,
    job_id: str,
    bgr: Any,
    observations: list[Any],
    progress: float,
    roi: list[float] | tuple[float, ...] | None = None,
) -> None:
    """Resize + JPEG-encode + publish (runs on the encoder thread). Never raises."""

    try:
        height, width = bgr.shape[:2]
        if width <= 0 or height <= 0:
            return
        scale = _PREVIEW_WIDTH / width
        pw, ph = _PREVIEW_WIDTH, max(1, round(height * scale))
        small = cv2.resize(bgr, (pw, ph))
        ok, buf = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), _JPEG_QUALITY])
        if not ok:
            return

        boxes: list[dict[str, Any]] = []
        for obs in observations:
            vb = obs.vehicle_bbox
            boxes.append(
                {
                    "k": "v",
                    "x1": round(vb[0] * scale),
                    "y1": round(vb[1] * scale),
                    "x2": round(vb[2] * scale),
                    "y2": round(vb[3] * scale),
                    "t": obs.vehicle_label,
                }
            )
            pb = obs.plate_bbox
            if pb is not None:
                boxes.append(
                    {
                        "k": "p",
                        "x1": round(pb[0] * scale),
                        "y1": round(pb[1] * scale),
                        "x2": round(pb[2] * scale),
                        "y2": round(pb[3] * scale),
                        "t": obs.reading.raw_text if obs.reading is not None else "",
                    }
                )

        payload: dict[str, Any] = {
            "progress": round(progress, 1),
            "w": pw,
            "h": ph,
            "img": base64.b64encode(buf).decode("ascii"),
            "boxes": boxes,
        }
        # The drawn ROI (normalized 0-1) → preview pixels, so the browser can outline the scan zone.
        if roi is not None and len(roi) == 4:
            payload["roi"] = [
                round(roi[0] * pw),
                round(roi[1] * ph),
                round(roi[2] * pw),
                round(roi[3] * ph),
            ]
        rconn.set(live_key(job_id), json.dumps(payload), ex=30)
    except Exception:
        # A preview hiccup must never affect the offline pipeline.
        pass


def should_publish_preview(job_id: str) -> bool:
    """Throttle gate for the smoothing preview — checked BEFORE converting a frame to ndarray so we
    never pay the conversion for a frame we'd drop. Also reset by publish_frame, so an intermediate
    frame never overwrites a fresh box frame too soon."""

    now = time.monotonic()
    if now - _last_emit.get(job_id, 0.0) < _MIN_INTERVAL_S:
        return False
    _last_emit[job_id] = now
    return True


def publish_preview(
    rconn: Any, job_id: str, bgr: Any, progress: float, roi: Any = None
) -> None:
    """Queue an image-only INTERMEDIATE frame for the background encoder to smooth the video between
    detections. DROPPED if the encoder is still busy, so it never queues up / blocks anything."""

    try:
        if _busy(job_id):
            return
        _inflight[job_id] = _encoder.submit(_encode_preview, rconn, job_id, bgr, progress, roi)
    except Exception:
        pass


def _encode_preview(
    rconn: Any, job_id: str, bgr: Any, progress: float, roi: Any = None
) -> None:
    """Image-only INTERMEDIATE frame (no boxes) — smooths the live video between detected frames.
    The frontend keeps the last boxes when a payload has no 'boxes' field. Never raises."""

    try:
        height, width = bgr.shape[:2]
        if width <= 0 or height <= 0:
            return
        scale = _PREVIEW_WIDTH / width
        pw, ph = _PREVIEW_WIDTH, max(1, round(height * scale))
        small = cv2.resize(bgr, (pw, ph))
        ok, buf = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), _JPEG_QUALITY])
        if not ok:
            return
        payload: dict[str, Any] = {
            "progress": round(progress, 1),
            "w": pw,
            "h": ph,
            "img": base64.b64encode(buf).decode("ascii"),
        }
        if roi is not None and len(roi) == 4:
            payload["roi"] = [
                round(roi[0] * pw),
                round(roi[1] * ph),
                round(roi[2] * pw),
                round(roi[3] * ph),
            ]
        rconn.set(live_key(job_id), json.dumps(payload), ex=30)
    except Exception:
        pass


def _small_jpeg(bgr: Any, target_width: int, quality: int) -> str:
    h, w = bgr.shape[:2]
    if w <= 0 or h <= 0 or bgr.size == 0:
        return ""
    scale = target_width / w
    resized = cv2.resize(bgr, (target_width, max(1, round(h * scale))))
    ok, buf = cv2.imencode(".jpg", resized, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return base64.b64encode(buf).decode("ascii") if ok else ""


def publish_event(rconn: Any, storage_root: Any, job_id: str, event: Any) -> None:
    """Append a detected-vehicle card (plate + crop + full frame thumbnail) to Redis. Never raises."""

    try:
        best = event.best_observation
        if best is None or not best.full_frame_key:
            return
        path = storage_root / best.full_frame_key
        img = cv2.imread(str(path))
        if img is None:
            return
        crop_b64 = ""
        pb = best.plate_bbox
        if pb is not None:
            x1, y1, x2, y2 = (max(0, int(v)) for v in pb)
            if x2 > x1 and y2 > y1:
                # Sharper crop: bigger + higher quality so the plate is legible, not a soft blob.
                crop_b64 = _small_jpeg(img[y1:y2, x1:x2], 220, 88)
        card = json.dumps(
            {
                "code": event.track_code,
                "plate": event.normalized_plate or "",
                "crop": crop_b64,
                "full": _small_jpeg(img, 280, 72),
                "ts": int(getattr(event, "start_timestamp_ms", 0) or 0),
                "end": int(getattr(event, "end_timestamp_ms", 0) or 0),
            }
        )
        key = events_key(job_id)
        rconn.rpush(key, card)
        rconn.expire(key, 300)
    except Exception:
        pass


def clear(rconn: Any, job_id: str) -> None:
    try:
        rconn.delete(live_key(job_id), events_key(job_id))
    except Exception:
        pass
