from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VideoMetadata:
    source_path: str
    source_name: str
    sha256: str
    size_bytes: int
    mime_type: str
    format_name: str | None
    width: int
    height: int
    duration_ms: int | None
    frame_count: int | None
    fps: float | None
    codec: str | None
    pixel_format: str | None
    time_base: str
    is_variable_frame_rate: bool | None = None


@dataclass(frozen=True)
class DecodedFrame:
    frame_index: int
    pts: int
    timestamp_us: int
    width: int
    height: int
    image: Any = field(repr=False, compare=False)

    @property
    def timestamp_ms(self) -> int:
        return round(self.timestamp_us / 1_000)


@dataclass(frozen=True)
class EvidenceFrame:
    frame_index: int
    pts: int
    timestamp_us: int
    target_timestamp_us: int
    width: int
    height: int
    storage_key: str
    sha256: str
    size_bytes: int

    @property
    def timestamp_ms(self) -> int:
        return round(self.timestamp_us / 1_000)


@dataclass(frozen=True)
class EvidenceManifest:
    schema_version: str
    job_id: str
    created_at: str
    pipeline_version: str
    sample_rate: float
    jpeg_quality: int
    source: VideoMetadata
    frames: tuple[EvidenceFrame, ...]
    decoded_frame_count: int
    timing_deltas_us: tuple[int, ...]
    manifest_path: Path = field(repr=False, compare=False)
