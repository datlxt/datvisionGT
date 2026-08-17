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
from typing import Any

import cv2

# Cap the publish rate so JPEG-encoding a preview never steals cycles from detection, and shrink the
# frame hard — the browser only needs a small picture to draw boxes on.
_MIN_INTERVAL_S = 0.18  # ~5 fps
_PREVIEW_WIDTH = 480
_JPEG_QUALITY = 70

# Per-process wall-clock of the last publish, keyed by job id (throttle state).
_last_emit: dict[str, float] = {}


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
    """Publish one preview snapshot to Redis. Never raises."""

    try:
        now = time.monotonic()
        if now - _last_emit.get(job_id, 0.0) < _MIN_INTERVAL_S:
            return
        _last_emit[job_id] = now

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
