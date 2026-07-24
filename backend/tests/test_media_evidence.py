import hashlib
import json
from pathlib import Path

import pytest

from app.vision.geometry import BoundingBoxError, validate_bbox_xyxy
from app.vision.media.evidence import EvidenceExtractor, validate_evidence_manifest
from app.vision.media.sampling import FrameTimingAnalyzer, TimestampSampler
from app.vision.media.types import DecodedFrame, VideoMetadata


class FakeVideoFrame:
    def __init__(self, color: tuple[int, int, int]) -> None:
        self.color = color

    def to_image(self):
        color = self.color

        class FakeImage:
            def save(self, output, **kwargs) -> None:
                del kwargs
                output.write(b"fake-jpeg:" + bytes(color))

        return FakeImage()


class FakeReader:
    def probe(self, source_path: Path) -> VideoMetadata:
        return VideoMetadata(
            source_path=str(source_path),
            source_name=source_path.name,
            sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
            size_bytes=123,
            mime_type="video/mp4",
            format_name="mp4",
            width=16,
            height=8,
            duration_ms=500,
            frame_count=4,
            fps=10.0,
            codec="h264",
            pixel_format="yuv420p",
            time_base="1/1000",
        )

    def iter_frames(self, source_path: Path):
        del source_path
        for index, timestamp_us in enumerate((0, 100_000, 250_000, 500_000)):
            yield DecodedFrame(
                frame_index=index,
                pts=index,
                timestamp_us=timestamp_us,
                width=16,
                height=8,
                image=FakeVideoFrame((index * 30, 0, 0)),
            )


def test_timestamp_sampler_selects_by_presentation_time() -> None:
    sampler = TimestampSampler(4.0)
    assert sampler.select(0) == 0
    assert sampler.select(100_000) is None
    assert sampler.select(250_000) == 250_000
    assert sampler.select(510_000) == 500_000


def test_timing_analyzer_detects_cfr_and_vfr() -> None:
    cfr = FrameTimingAnalyzer()
    for timestamp in (0, 40_000, 80_000, 120_000):
        cfr.observe(timestamp)
    assert cfr.is_variable_frame_rate is False

    vfr = FrameTimingAnalyzer()
    for timestamp in (0, 40_000, 100_000, 140_000):
        vfr.observe(timestamp)
    assert vfr.is_variable_frame_rate is True


def test_evidence_extractor_writes_deterministic_job_scoped_frames(tmp_path: Path) -> None:
    extractor = EvidenceExtractor(reader=FakeReader())
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"sample")
    manifest = extractor.extract(
        source_path=source,
        storage_root=tmp_path / "storage",
        job_id="job-test-001",
        sample_rate=4.0,
    )

    assert len(manifest.frames) == 3
    assert [frame.timestamp_us for frame in manifest.frames] == [0, 250_000, 500_000]
    assert all(frame.sha256 for frame in manifest.frames)
    assert manifest.manifest_path.is_file()
    saved = json.loads(manifest.manifest_path.read_text(encoding="utf-8"))
    assert saved["source"]["sha256"] == hashlib.sha256(b"sample").hexdigest()
    assert saved["frames"][1]["storage_key"].startswith("jobs/job-test-001/frames/")
    assert len(list(manifest.manifest_path.parent.joinpath("frames").glob("*.jpg"))) == 3
    assert validate_evidence_manifest(manifest.manifest_path) == []


def test_evidence_validator_detects_cross_job_reference(tmp_path: Path) -> None:
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"sample")
    manifest = EvidenceExtractor(reader=FakeReader()).extract(
        source_path=source,
        storage_root=tmp_path / "storage",
        job_id="job-one",
    )
    value = json.loads(manifest.manifest_path.read_text(encoding="utf-8"))
    value["frames"][0]["storage_key"] = "jobs/job-two/frames/stolen.jpg"
    manifest.manifest_path.write_text(json.dumps(value), encoding="utf-8")
    errors = validate_evidence_manifest(manifest.manifest_path)
    assert any("not scoped" in error for error in errors)


def test_existing_job_cannot_be_rebound_to_a_different_config(tmp_path: Path) -> None:
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"sample")
    extractor = EvidenceExtractor(reader=FakeReader())
    extractor.extract(
        source_path=source,
        storage_root=tmp_path / "storage",
        job_id="immutable-job",
        sample_rate=4.0,
    )
    with pytest.raises(ValueError, match="already bound"):
        extractor.extract(
            source_path=source,
            storage_root=tmp_path / "storage",
            job_id="immutable-job",
            sample_rate=2.0,
        )


def test_bbox_validation_rejects_invalid_geometry() -> None:
    bbox = validate_bbox_xyxy([10.1, 5.2, 50.4, 25.7], frame_width=100, frame_height=50)
    assert (bbox.x1, bbox.y1, bbox.x2, bbox.y2) == (10, 5, 50, 26)
    with pytest.raises(BoundingBoxError):
        validate_bbox_xyxy([-1, 0, 20, 20], frame_width=100, frame_height=50)
    with pytest.raises(BoundingBoxError):
        validate_bbox_xyxy([20, 20, 10, 30], frame_width=100, frame_height=50)
