from __future__ import annotations

import hashlib
import mimetypes
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path
from typing import Any

from app.vision.media.types import DecodedFrame, VideoMetadata


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


class PyAVVideoReader:
    """Decode video frames while preserving source PTS and presentation time."""

    def probe(self, source_path: Path, *, sha256: str | None = None) -> VideoMetadata:
        import av

        path = source_path.resolve(strict=True)
        with av.open(str(path)) as container:
            stream = self._first_video_stream(container)
            duration_ms = self._duration_ms(container, stream)
            fps = float(stream.average_rate) if stream.average_rate else None
            codec_context = stream.codec_context
            pixel_format = codec_context.format.name if codec_context.format else None
            mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            return VideoMetadata(
                source_path=str(path),
                source_name=path.name,
                sha256=sha256 or sha256_file(path),
                size_bytes=path.stat().st_size,
                mime_type=mime_type,
                format_name=container.format.name if container.format else None,
                width=codec_context.width,
                height=codec_context.height,
                duration_ms=duration_ms,
                frame_count=stream.frames or None,
                fps=fps,
                codec=codec_context.name,
                pixel_format=pixel_format,
                time_base=str(stream.time_base),
            )

    def iter_frames(self, source_path: Path) -> Iterator[DecodedFrame]:
        import av

        path = source_path.resolve(strict=True)
        with av.open(str(path)) as container:
            stream = self._first_video_stream(container)
            # Multi-threaded decode: identical frames in presentation order, just faster — speeds the
            # condense scan and the main pipeline's frame extraction alike.
            stream.thread_type = "AUTO"
            time_base = stream.time_base
            origin_pts = stream.start_time
            decoded_index = 0

            for frame in container.decode(stream):
                if frame.pts is None:
                    continue
                if origin_pts is None:
                    origin_pts = frame.pts

                relative_time = Fraction(frame.pts - origin_pts) * time_base
                timestamp_us = max(0, round(relative_time * 1_000_000))
                yield DecodedFrame(
                    frame_index=decoded_index,
                    pts=frame.pts,
                    timestamp_us=timestamp_us,
                    width=frame.width,
                    height=frame.height,
                    image=frame,
                )
                decoded_index += 1

    @staticmethod
    def _first_video_stream(container: Any) -> Any:
        if not container.streams.video:
            raise ValueError("Source does not contain a video stream")
        return container.streams.video[0]

    @staticmethod
    def _duration_ms(container: Any, stream: Any) -> int | None:
        if stream.duration is not None and stream.time_base is not None:
            return max(0, round(Fraction(stream.duration) * stream.time_base * 1_000))
        if container.duration is not None:
            return max(0, round(container.duration / 1_000))
        return None
