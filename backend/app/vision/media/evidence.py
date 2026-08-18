from __future__ import annotations

import hashlib
import io
import json
import os
import re
from collections.abc import Callable, Iterator
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.vision.media.reader import PyAVVideoReader, sha256_file
from app.vision.media.sampling import FrameTimingAnalyzer, TimestampSampler
from app.vision.media.types import EvidenceFrame, EvidenceManifest

SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _jpeg_bytes(video_frame: object, quality: int) -> bytes:
    image = video_frame.to_image()
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=quality, optimize=True)
    return output.getvalue()


class EvidenceExtractor:
    def __init__(self, reader: PyAVVideoReader | None = None) -> None:
        self.reader = reader or PyAVVideoReader()

    def extract(
        self,
        *,
        source_path: Path,
        storage_root: Path,
        job_id: str,
        sample_rate: float = 4.0,
        pipeline_version: str = "evidence-v1",
        jpeg_quality: int = 92,
        max_frames: int | None = None,
        progress_callback: Callable[[int, int | None], None] | None = None,
        frame_callback: Callable[[Any, int, int | None], None] | None = None,
    ) -> EvidenceManifest:
        """Extract evidence to disk and return the manifest (drains iter_extract)."""

        generator = self.iter_extract(
            source_path=source_path,
            storage_root=storage_root,
            job_id=job_id,
            sample_rate=sample_rate,
            pipeline_version=pipeline_version,
            jpeg_quality=jpeg_quality,
            max_frames=max_frames,
            progress_callback=progress_callback,
            frame_callback=frame_callback,
        )
        while True:
            try:
                next(generator)
            except StopIteration as stop:
                return stop.value

    def iter_extract(
        self,
        *,
        source_path: Path,
        storage_root: Path,
        job_id: str,
        sample_rate: float = 4.0,
        pipeline_version: str = "evidence-v1",
        jpeg_quality: int = 92,
        max_frames: int | None = None,
        progress_callback: Callable[[int, int | None], None] | None = None,
        frame_callback: Callable[[Any, int, int | None], None] | None = None,
        preview_callback: Callable[[Any, int, int | None], None] | None = None,
    ) -> "Iterator[tuple[Any, EvidenceFrame]]":
        """Extract evidence AND yield each sampled frame as it is saved — so detection can run in
        the SAME pass (boxes from the first frame, no decode→save→reload roundtrip). Yields
        ``(bgr_ndarray, EvidenceFrame)`` per sampled frame and RETURNS the completed manifest."""

        if not SAFE_IDENTIFIER.fullmatch(job_id):
            raise ValueError("job_id must contain only letters, numbers, underscores, or hyphens")
        if not 1 <= jpeg_quality <= 100:
            raise ValueError("jpeg_quality must be between 1 and 100")
        if max_frames is not None and max_frames <= 0:
            raise ValueError("max_frames must be greater than zero")

        metadata = self.reader.probe(source_path)
        job_root = storage_root.resolve() / "jobs" / job_id
        manifest_path = job_root / "evidence-manifest.json"
        self._validate_existing_job_binding(
            manifest_path=manifest_path,
            source_sha256=metadata.sha256,
            sample_rate=sample_rate,
            pipeline_version=pipeline_version,
            jpeg_quality=jpeg_quality,
        )
        frames_root = job_root / "frames"
        frames_root.mkdir(parents=True, exist_ok=True)

        sampler = TimestampSampler(sample_rate)
        timing = FrameTimingAnalyzer()
        evidence_frames: list[EvidenceFrame] = []
        decoded_count = 0

        for frame in self.reader.iter_frames(source_path):
            decoded_count += 1
            if progress_callback is not None and decoded_count % 250 == 0:
                progress_callback(decoded_count, metadata.frame_count)
            timing.observe(frame.timestamp_us)
            target_timestamp_us = sampler.select(frame.timestamp_us)
            if target_timestamp_us is None:
                # Not a sampled frame: hand it to the smoothing preview (image-only) so the live
                # video plays at a higher fps than the sample rate. The callback throttles BEFORE
                # converting, so we don't pay the ndarray cost for frames it drops.
                if preview_callback is not None:
                    try:
                        preview_callback(frame.image, decoded_count, metadata.frame_count)
                    except Exception:
                        pass
                continue

            filename = f"{len(evidence_frames):06d}_{frame.timestamp_us:012d}.jpg"
            storage_key = f"jobs/{job_id}/frames/{filename}"
            payload = _jpeg_bytes(frame.image, jpeg_quality)
            _atomic_write(frames_root / filename, payload)
            evidence_frame = EvidenceFrame(
                frame_index=frame.frame_index,
                pts=frame.pts,
                timestamp_us=frame.timestamp_us,
                target_timestamp_us=target_timestamp_us,
                width=frame.width,
                height=frame.height,
                storage_key=storage_key,
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
            )
            evidence_frames.append(evidence_frame)
            bgr = frame.image.to_ndarray(format="bgr24")
            # Hand the just-sampled frame to an optional consumer (the live preview) so the video is
            # visible from the start of extraction instead of a dead wait. Best-effort.
            if frame_callback is not None:
                try:
                    frame_callback(bgr, decoded_count, metadata.frame_count)
                except Exception:
                    pass
            # Feed the SAME decoded frame to the detection pass in the caller — no reload needed.
            yield bgr, evidence_frame
            if max_frames is not None and len(evidence_frames) >= max_frames:
                break

        if progress_callback is not None:
            progress_callback(decoded_count, metadata.frame_count)

        if sha256_file(source_path.resolve(strict=True)) != metadata.sha256:
            raise RuntimeError("Source video changed while evidence was being extracted")

        metadata = replace(metadata, is_variable_frame_rate=timing.is_variable_frame_rate)
        manifest = EvidenceManifest(
            schema_version="1.0",
            job_id=job_id,
            created_at=datetime.now(UTC).isoformat(),
            pipeline_version=pipeline_version,
            sample_rate=sample_rate,
            jpeg_quality=jpeg_quality,
            source=metadata,
            frames=tuple(evidence_frames),
            decoded_frame_count=decoded_count,
            timing_deltas_us=timing.representative_deltas_us,
            manifest_path=manifest_path,
        )
        serializable = asdict(manifest)
        serializable.pop("manifest_path")
        _atomic_write(manifest_path, _json_bytes(serializable))
        return manifest

    @staticmethod
    def _validate_existing_job_binding(
        *,
        manifest_path: Path,
        source_sha256: str,
        sample_rate: float,
        pipeline_version: str,
        jpeg_quality: int,
    ) -> None:
        if not manifest_path.exists():
            return
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        binding = (
            existing.get("source", {}).get("sha256"),
            existing.get("sample_rate"),
            existing.get("pipeline_version"),
            existing.get("jpeg_quality", 92),
        )
        requested = (source_sha256, sample_rate, pipeline_version, jpeg_quality)
        if binding != requested:
            raise ValueError(
                "job_id is already bound to a different source or evidence configuration"
            )


def validate_evidence_manifest(
    manifest_path: Path,
    *,
    storage_root: Path | None = None,
    verify_hashes: bool = True,
) -> list[str]:
    errors: list[str] = []
    path = manifest_path.resolve(strict=True)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [str(exc)]

    if not isinstance(manifest, dict):
        return ["Evidence manifest must be a JSON object"]
    job_id = manifest.get("job_id")
    frames = manifest.get("frames")
    if not isinstance(job_id, str) or not SAFE_IDENTIFIER.fullmatch(job_id):
        errors.append("Manifest has an invalid job_id")
    if not isinstance(frames, list):
        return errors + ["Manifest frames must be a list"]

    root = storage_root.resolve() if storage_root else path.parents[2]
    expected_prefix = f"jobs/{job_id}/frames/"
    expected_frames_root = (root / "jobs" / str(job_id) / "frames").resolve()
    previous_timestamp = -1
    seen_storage_keys: set[str] = set()
    for index, frame in enumerate(frames):
        prefix = f"frame {index}"
        if not isinstance(frame, dict):
            errors.append(f"{prefix}: value must be an object")
            continue
        storage_key = frame.get("storage_key")
        if not isinstance(storage_key, str) or not storage_key.startswith(expected_prefix):
            errors.append(f"{prefix}: storage_key is not scoped to job {job_id}")
            continue
        if storage_key in seen_storage_keys:
            errors.append(f"{prefix}: duplicate storage_key")
        seen_storage_keys.add(storage_key)
        artifact_path = (root / Path(storage_key)).resolve()
        if not artifact_path.is_relative_to(expected_frames_root):
            errors.append(f"{prefix}: storage_key escapes the job frame directory")
            continue
        if not artifact_path.is_file():
            errors.append(f"{prefix}: evidence file is missing")
            continue

        timestamp_us = frame.get("timestamp_us")
        if not isinstance(timestamp_us, int) or timestamp_us < previous_timestamp:
            errors.append(f"{prefix}: timestamps are not monotonic")
        else:
            previous_timestamp = timestamp_us
        if artifact_path.stat().st_size != frame.get("size_bytes"):
            errors.append(f"{prefix}: size does not match manifest")
        if verify_hashes and hashlib.sha256(artifact_path.read_bytes()).hexdigest() != frame.get(
            "sha256"
        ):
            errors.append(f"{prefix}: SHA-256 does not match manifest")
    return errors
