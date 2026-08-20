from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rq import get_current_job

from app.core.config import get_settings
from app.vision.media.condense import (
    plan_segments,
    render_condensed_video,
    scan_active_timestamps,
    total_kept_ms,
)
from app.vision.media.reader import PyAVVideoReader
from app.vision.plate.motion_vehicle import CompositeVehicleDetector, FixedCameraMotionDetector
from app.vision.plate.yolox_vehicle import YoloXMotorcycleDetector


def _model_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Model path escaped MODEL_ROOT")
    return path


class _Progress:
    """Throttled writer for RQ job progress so we don't hammer Redis every frame."""

    def __init__(self) -> None:
        self._job = get_current_job()
        self._last = -1.0

    def set(self, stage: str, fraction: float) -> None:
        fraction = min(1.0, max(0.0, fraction))
        if self._job is None or (fraction - self._last < 0.01 and fraction < 1.0):
            return
        self._last = fraction
        self._job.meta["stage"] = stage
        self._job.meta["progress"] = round(fraction, 3)
        self._job.save_meta()


def condense_video(
    condense_id: str,
    source_name: str,
    *,
    min_gap_seconds: int = 15,
    pad_ms: int = 750,
    # Larger trailing pad so a vehicle's full EXIT is kept — the detector often loses it while it's
    # still leaving the frame, and 750ms wasn't enough to cover the rest (the tail got cut).
    trail_pad_ms: int = 2500,
    min_active_ms: int = 500,
    sample_every_ms: int = 250,
    roi: list[float] | None = None,
) -> dict[str, Any]:
    """Scan an uploaded video for vehicle activity and re-encode only the busy segments.

    ``roi`` (normalized x1,y1,x2,y2) restricts activity to the chosen lane so an adjacent lane
    no longer triggers a keep.
    """

    settings = get_settings()
    base = settings.storage_root / "condense" / condense_id
    source = base / "source.mp4"
    out = base / "condensed.mp4"

    reader = PyAVVideoReader()
    meta = reader.probe(source)
    duration_ms = meta.duration_ms or 0

    model_root = settings.model_root.resolve()
    detector = CompositeVehicleDetector(
        [
            YoloXMotorcycleDetector(
                _model_path(model_root, settings.vehicle_model_path),
                intra_op_threads=settings.model_intra_op_threads,
            ),
            FixedCameraMotionDetector(),
        ]
    )

    progress = _Progress()
    progress.set("scanning", 0.0)

    def on_scan(timestamp_ms: int) -> None:
        if duration_ms:
            progress.set("scanning", 0.7 * timestamp_ms / duration_ms)

    roi_box = (
        (float(roi[0]), float(roi[1]), float(roi[2]), float(roi[3]))
        if roi and len(roi) == 4
        else None
    )
    active = scan_active_timestamps(
        source,
        detector,
        sample_every_ms=sample_every_ms,
        roi=roi_box,
        on_progress=on_scan,
    )
    segments = plan_segments(
        active,
        duration_ms=duration_ms,
        pad_ms=pad_ms,
        trail_pad_ms=trail_pad_ms,
        merge_gap_ms=min_gap_seconds * 1000,
        min_active_ms=min_active_ms,
    )

    frames = 0
    if segments:
        progress.set("rendering", 0.7)

        def on_render(timestamp_ms: int) -> None:
            if duration_ms:
                progress.set("rendering", 0.7 + 0.3 * timestamp_ms / duration_ms)

        frames = render_condensed_video(source, segments, out, on_progress=on_render)

    kept_ms = total_kept_ms(segments)
    result = {
        "condense_id": condense_id,
        "source_name": source_name,
        "status": "completed" if segments else "empty",
        "source_duration_ms": duration_ms,
        "condensed_duration_ms": kept_ms,
        "cut_ms": max(0, duration_ms - kept_ms),
        "segment_count": len(segments),
        "segments": [[segment.start_ms, segment.end_ms] for segment in segments],
        "frames": frames,
        "width": meta.width,
        "height": meta.height,
        "fps": meta.fps,
        "min_gap_seconds": min_gap_seconds,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    (base / "result.json").write_text(json.dumps(result))
    progress.set("completed", 1.0)
    return result
