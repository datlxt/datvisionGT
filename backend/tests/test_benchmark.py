import hashlib
import json
from pathlib import Path

import pytest

from app.benchmark.dataset import create_dataset, validate_annotations
from app.benchmark.evaluator import evaluate_predictions


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )


def test_annotation_validator_rejects_out_of_bounds_bbox(tmp_path: Path) -> None:
    annotations = tmp_path / "annotations.jsonl"
    _write_jsonl(
        annotations,
        [
            {
                "image_id": "one",
                "width": 100,
                "height": 50,
                "plates": [
                    {
                        "bbox": [90, 10, 20, 20],
                        "plate_text": "29A112345",
                        "layout": "two_line",
                    }
                ],
            }
        ],
    )
    errors = validate_annotations(annotations)
    assert any("bbox" in error for error in errors)


def test_dataset_creation_preserves_evidence_traceability(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    job_root = storage / "jobs" / "job-one"
    frames_root = job_root / "frames"
    frames_root.mkdir(parents=True)
    frames = []
    for index in range(5):
        image = frames_root / f"{index}.jpg"
        image.write_bytes(f"image-{index}".encode())
        frames.append(
            {
                "frame_index": index,
                "pts": index * 25,
                "timestamp_us": index * 250_000,
                "width": 100,
                "height": 50,
                "storage_key": f"jobs/job-one/frames/{index}.jpg",
                "sha256": hashlib.sha256(f"image-{index}".encode()).hexdigest(),
            }
        )
    evidence = {
        "job_id": "job-one",
        "source": {"sha256": "b" * 64},
        "frames": frames,
    }
    evidence_path = job_root / "evidence-manifest.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    manifest_path = create_dataset(
        evidence_manifest_path=evidence_path,
        output_root=storage / "datasets",
        dataset_id="plate-v1",
        every_nth_frame=2,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["selection"]["selected_images"] == 3
    annotations = (manifest_path.parent / "annotations" / "plates.jsonl").read_text(
        encoding="utf-8"
    )
    assert '"timestamp_us": 500000' in annotations


def test_evaluator_reports_detection_and_end_to_end_accuracy(tmp_path: Path) -> None:
    ground_truth = tmp_path / "ground-truth.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    _write_jsonl(
        ground_truth,
        [
            {
                "image_id": "one",
                "width": 200,
                "height": 100,
                "plates": [
                    {
                        "bbox": [10, 10, 50, 30],
                        "plate_text": "29A112345",
                        "layout": "two_line",
                        "difficulty": "easy",
                    }
                ],
            },
            {
                "image_id": "two",
                "width": 200,
                "height": 100,
                "plates": [
                    {
                        "bbox": [20, 20, 40, 20],
                        "plate_text": "30B167890",
                        "layout": "two_line",
                        "difficulty": "hard",
                    }
                ],
            },
        ],
    )
    _write_jsonl(
        predictions,
        [
            {
                "image_id": "one",
                "plates": [
                    {
                        "bbox": [10, 10, 50, 30],
                        "confidence": 0.9,
                        "plate_text": "29A112345",
                    },
                    {"bbox": [120, 50, 20, 10], "confidence": 0.2, "plate_text": ""},
                ],
            }
        ],
    )

    metrics = evaluate_predictions(
        ground_truth_path=ground_truth,
        predictions_path=predictions,
    )
    assert metrics["detection"]["precision"] == pytest.approx(0.5)
    assert metrics["detection"]["recall"] == pytest.approx(0.5)
    assert metrics["recognition"]["exact_accuracy_on_matched"] == pytest.approx(1.0)
    assert metrics["recognition"]["end_to_end_exact_accuracy"] == pytest.approx(0.5)
