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
import threading
import time
from collections import deque
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


class LivePreviewPublisher:
    """Publishes the smoothing preview at a STEADY cadence, decoupled from the decode/detect loop.

    The offline loop produces frames in BURSTS: it decodes a batch of frames, then spends most of the
    time DETECTING (during which nothing is decoded, so nothing new is shown). Publishing previews
    straight from the decode callback therefore made the live video stutter — a rush of frames then a
    freeze on each detection step — worst on car clips where detection is the long part.

    Instead, the decode callback just APPENDS each decoded frame to a small bounded ring (cheap, no
    encode); this publisher's own thread pops one frame every ``interval`` and encodes+publishes it.
    So a decode burst fills the ring and the following detection gap DRAINS it at a constant rate —
    the motion plays back evenly instead of rushing then freezing. The ring is capped so latency
    stays bounded (oldest un-played frames are dropped, which just speeds the playback slightly). It
    shows REAL decoded frames in order (so detection boxes still line up) and is fully best-effort —
    every failure is swallowed and it never touches the pipeline.
    """

    def __init__(
        self,
        rconn: Any,
        job_id: str,
        roi: Any,
        interval: float = _MIN_INTERVAL_S,
        buffer: int = 15,
    ) -> None:
        self._rconn = rconn
        self._job_id = job_id
        self._roi = roi
        self._interval = interval
        self._cap = buffer  # ~1.5 s of playback at 10 fps — bounds how far live lags the pipeline
        self._frames: deque[tuple[Any, float]] = deque()
        self._boxes: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="live-preview", daemon=True)

    def start(self) -> "LivePreviewPublisher":
        self._thread.start()
        return self

    def update(self, av_frame: Any, progress: float) -> None:
        """Queue a decoded frame (av.VideoFrame) — called per non-sampled frame. Cheap: just stores a
        reference; the conversion + encode happen on this publisher's thread. Oldest un-played frames
        are dropped when the ring is full so the preview never lags more than ``_cap`` frames."""

        with self._lock:
            self._frames.append((av_frame, progress))
            while len(self._frames) > self._cap:
                self._frames.popleft()

    def set_boxes(self, boxes: list[dict[str, Any]]) -> None:
        """Record the latest detection boxes (already scaled to the preview width). They ride along
        in EVERY published frame — so the image comes from ONE monotonic source (this publisher) and
        the boxes just overlay it. That's what fixes the forward/backward jump: previously the box
        frames wrote their OWN (detection-frontier) image while the smooth preview wrote a slightly
        different-time image, and the two fought."""

        with self._lock:
            self._boxes = boxes

    def stop(self) -> None:
        self._stop.set()
        try:
            self._thread.join(timeout=1.5)
        except Exception:
            pass

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            with self._lock:
                item = self._frames.popleft() if self._frames else None
                boxes = self._boxes
            if item is None:
                continue  # ring empty (pipeline mid-detection with no buffered frames) — hold
            try:
                bgr = item[0].to_ndarray(format="bgr24")
            except Exception:
                continue
            _encode_preview(self._rconn, self._job_id, bgr, item[1], self._roi, boxes)


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


def build_boxes(bgr: Any, observations: list[Any]) -> list[dict[str, Any]]:
    """Scale the detection boxes for a frame to the preview width, for the client overlay. Returned
    to the LivePreviewPublisher via set_boxes so the boxes ride inside the (single, monotonic) image
    stream instead of being published as their own competing frame."""

    boxes: list[dict[str, Any]] = []
    try:
        height, width = bgr.shape[:2]
        if width <= 0 or height <= 0:
            return boxes
        scale = _PREVIEW_WIDTH / width
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
    except Exception:
        return boxes
    return boxes


def _encode_preview(
    rconn: Any,
    job_id: str,
    bgr: Any,
    progress: float,
    roi: Any = None,
    boxes: list[dict[str, Any]] | None = None,
) -> None:
    """Encode + publish one preview frame — the SINGLE writer of the live image. The current
    detection boxes (if any) ride along so they overlay this frame on the client. Never raises."""

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
            "boxes": boxes or [],
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
