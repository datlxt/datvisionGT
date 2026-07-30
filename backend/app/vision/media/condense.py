from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Protocol

from numpy.typing import NDArray


class ActivityDetector(Protocol):
    def detect(self, frame: NDArray) -> list: ...


ProgressCallback = Callable[[int], None]


@dataclass(frozen=True, slots=True)
class Segment:
    """A [start, end] window of the source video to keep in the condensed cut."""

    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def plan_segments(
    active_ms: list[int],
    *,
    duration_ms: int,
    pad_ms: int = 750,
    merge_gap_ms: int = 2_000,
    min_active_ms: int = 500,
) -> list[Segment]:
    """Turn per-frame "vehicle present" timestamps into keep-segments for a condensed cut.

    Nearby activity is joined (a bike briefly missed between sampled frames must not split
    its pass), isolated blips shorter than ``min_active_ms`` are dropped as noise, and each
    kept run is padded so the vehicle enters and leaves fully. Cutting is destructive, so
    the planner errs toward keeping: padding and gap-merging only ever grow segments.
    """

    if not active_ms or duration_ms <= 0:
        return []
    ordered = sorted(active_ms)
    # 1. Group activity into raw runs; a gap over merge_gap_ms starts a new run.
    runs: list[list[int]] = [[ordered[0], ordered[0]]]
    for timestamp in ordered[1:]:
        if timestamp - runs[-1][1] <= merge_gap_ms:
            runs[-1][1] = timestamp
        else:
            runs.append([timestamp, timestamp])
    # 2. Drop isolated noise blips (a lone detection with no sustained activity).
    runs = [run for run in runs if run[1] - run[0] >= min_active_ms]
    if not runs:
        return []
    # 3. Pad each run so the vehicle fully enters/leaves, clamped to the video bounds.
    padded = [
        [max(0, run[0] - pad_ms), min(duration_ms, run[1] + pad_ms)] for run in runs
    ]
    # 4. Merge windows that overlap once padded.
    merged: list[Segment] = [Segment(padded[0][0], padded[0][1])]
    for start, end in padded[1:]:
        if start <= merged[-1].end_ms:
            merged[-1] = Segment(merged[-1].start_ms, max(merged[-1].end_ms, end))
        else:
            merged.append(Segment(start, end))
    return merged


def total_kept_ms(segments: list[Segment]) -> int:
    return sum(segment.duration_ms for segment in segments)


def scan_active_timestamps(
    source_path: Path,
    detector: ActivityDetector,
    *,
    sample_every_ms: int = 250,
    on_progress: ProgressCallback | None = None,
) -> list[int]:
    """Sample the video and return timestamps (ms) where a vehicle or motion is present.

    The detector is fed frames in order (its motion background model needs a steady feed).
    Any hit — semantic vehicle OR motion — marks the sample active, so a hard-to-detect
    or briefly stationary vehicle is kept rather than cut.
    """

    from app.vision.media.reader import PyAVVideoReader

    reader = PyAVVideoReader()
    active: list[int] = []
    last_sample_ms = -(10**9)
    for frame in reader.iter_frames(source_path):
        timestamp_ms = frame.timestamp_us // 1000
        if timestamp_ms - last_sample_ms < sample_every_ms:
            continue
        last_sample_ms = timestamp_ms
        if detector.detect(frame.image.to_ndarray(format="bgr24")):
            active.append(timestamp_ms)
        if on_progress is not None:
            on_progress(timestamp_ms)
    return active


def render_condensed_video(
    source_path: Path,
    segments: list[Segment],
    out_path: Path,
    *,
    on_progress: ProgressCallback | None = None,
) -> int:
    """Re-encode only the kept segments into one continuous MP4; returns frames written.

    Frames outside every segment are dropped and the survivors get sequential PTS, so the
    output plays as a normal gap-free video. Re-encoding (not stream copy) keeps the cut
    frame-accurate regardless of keyframe positions.
    """

    import av

    if not segments:
        raise ValueError("Cannot render a condensed video with no segments")
    ranges = [(segment.start_ms, segment.end_ms) for segment in segments]
    written = 0
    with av.open(str(source_path)) as source:
        in_stream = source.streams.video[0]
        time_base = in_stream.time_base
        origin_pts = in_stream.start_time
        fps = int(round(float(in_stream.average_rate or 25)))
        frame_time_base = Fraction(1, fps)
        with av.open(str(out_path), "w") as output:
            out_stream = output.add_stream("libx264", rate=fps)
            out_stream.width = in_stream.codec_context.width
            out_stream.height = in_stream.codec_context.height
            out_stream.pix_fmt = "yuv420p"
            out_stream.codec_context.time_base = frame_time_base
            for frame in source.decode(in_stream):
                if frame.pts is None:
                    continue
                if origin_pts is None:
                    origin_pts = frame.pts
                timestamp_ms = float((frame.pts - origin_pts) * time_base * 1000)
                if on_progress is not None:
                    on_progress(int(timestamp_ms))
                if not any(low <= timestamp_ms <= high for low, high in ranges):
                    continue
                out_frame = frame.reformat(format="yuv420p")
                # Sequential PTS collapses the kept segments into one gap-free timeline.
                out_frame.pts = written
                out_frame.time_base = frame_time_base
                for packet in out_stream.encode(out_frame):
                    output.mux(packet)
                written += 1
            for packet in out_stream.encode(None):
                output.mux(packet)
    return written
