from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SAFE_DATASET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _split_name(position: int, total: int) -> str:
    ratio = position / max(total, 1)
    if ratio < 0.6:
        return "development"
    if ratio < 0.8:
        return "validation"
    return "test"


def create_dataset(
    *,
    evidence_manifest_path: Path,
    output_root: Path,
    dataset_id: str,
    every_nth_frame: int = 1,
    max_images: int | None = 300,
) -> Path:
    if every_nth_frame <= 0:
        raise ValueError("every_nth_frame must be greater than zero")
    if not SAFE_DATASET_ID.fullmatch(dataset_id):
        raise ValueError("dataset_id must contain only letters, numbers, underscores, or hyphens")
    if max_images is not None and max_images <= 0:
        raise ValueError("max_images must be greater than zero")

    evidence_path = evidence_manifest_path.resolve(strict=True)
    evidence = _load_json(evidence_path)
    all_frames = evidence.get("frames")
    if not isinstance(all_frames, list):
        raise ValueError("Evidence manifest does not contain a frame list")

    selected = all_frames[::every_nth_frame]
    if max_images is not None:
        selected = selected[:max_images]

    storage_root = evidence_path.parents[2]
    dataset_root = output_root.resolve() / dataset_id
    if dataset_root.exists():
        raise FileExistsError(
            f"Dataset already exists and will not be overwritten: {dataset_root}"
        )
    images_root = dataset_root / "images"
    annotations_root = dataset_root / "annotations"
    splits_root = dataset_root / "splits"
    images_root.mkdir(parents=True, exist_ok=True)
    annotations_root.mkdir(parents=True, exist_ok=True)
    splits_root.mkdir(parents=True, exist_ok=True)

    annotation_records: list[dict[str, Any]] = []
    split_members: dict[str, list[str]] = {
        "development": [],
        "validation": [],
        "test": [],
    }

    for position, frame in enumerate(selected):
        storage_key = frame.get("storage_key")
        if not isinstance(storage_key, str):
            raise ValueError("Evidence frame is missing storage_key")
        source_image = storage_root / Path(storage_key)
        if not source_image.is_file():
            raise FileNotFoundError(f"Evidence image is missing: {source_image}")
        if _sha256(source_image) != frame.get("sha256"):
            raise ValueError(f"Evidence image hash does not match: {source_image}")

        image_id = f"{evidence['job_id']}_{position:06d}"
        destination_name = f"{image_id}{source_image.suffix.lower()}"
        destination = images_root / destination_name
        shutil.copy2(source_image, destination)
        split = _split_name(position, len(selected))
        split_members[split].append(image_id)
        annotation_records.append(
            {
                "image_id": image_id,
                "file_name": f"images/{destination_name}",
                "width": frame["width"],
                "height": frame["height"],
                "split": split,
                "source": {
                    "job_id": evidence["job_id"],
                    "frame_index": frame["frame_index"],
                    "pts": frame["pts"],
                    "timestamp_us": frame["timestamp_us"],
                    "sha256": frame["sha256"],
                },
                "plates": [],
            }
        )

    annotations_path = annotations_root / "plates.jsonl"
    with annotations_path.open("w", encoding="utf-8", newline="\n") as output:
        for record in annotation_records:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")

    for split, members in split_members.items():
        (splits_root / f"{split}.txt").write_text(
            "".join(f"{image_id}\n" for image_id in members), encoding="utf-8"
        )

    manifest = {
        "schema_version": "1.0",
        "dataset_id": dataset_id,
        "created_at": datetime.now(UTC).isoformat(),
        "purpose": "smoke_benchmark",
        "source_evidence": {
            "path": str(evidence_path),
            "sha256": _sha256(evidence_path),
            "job_id": evidence["job_id"],
            "source_sha256": evidence["source"]["sha256"],
        },
        "selection": {
            "every_nth_frame": every_nth_frame,
            "max_images": max_images,
            "selected_images": len(annotation_records),
            "split_strategy": "contiguous_temporal_60_20_20",
        },
        "annotations": "annotations/plates.jsonl",
        "initial_annotation_sha256": _sha256(annotations_path),
    }
    manifest_path = dataset_root / "dataset-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest_path


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Line {line_number} must be a JSON object")
            records.append(value)
    return records


def validate_annotations(path: Path) -> list[str]:
    errors: list[str] = []
    seen_images: set[str] = set()
    valid_layouts = {"one_line", "two_line", "unknown"}
    valid_difficulties = {"easy", "medium", "hard"}

    try:
        records = load_jsonl(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [str(exc)]

    for record_index, record in enumerate(records, start=1):
        image_id = record.get("image_id")
        prefix = f"record {record_index}"
        if not isinstance(image_id, str) or not image_id:
            errors.append(f"{prefix}: image_id is required")
            continue
        if image_id in seen_images:
            errors.append(f"{prefix}: duplicate image_id {image_id}")
        seen_images.add(image_id)

        width = record.get("width")
        height = record.get("height")
        if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
            errors.append(f"{prefix}: width and height must be positive integers")
            continue

        plates = record.get("plates")
        if not isinstance(plates, list):
            errors.append(f"{prefix}: plates must be a list")
            continue

        for plate_index, plate in enumerate(plates, start=1):
            plate_prefix = f"{prefix}, plate {plate_index}"
            if not isinstance(plate, dict):
                errors.append(f"{plate_prefix}: annotation must be an object")
                continue
            bbox = plate.get("bbox")
            if not _valid_bbox(bbox, width, height):
                errors.append(f"{plate_prefix}: bbox must be [x, y, width, height] inside image")
            text = plate.get("plate_text")
            unreadable = plate.get("unreadable", False)
            ignored = plate.get("ignore", False)
            if not unreadable and not ignored and (not isinstance(text, str) or not text):
                errors.append(
                    f"{plate_prefix}: plate_text is required unless unreadable or ignored"
                )
            if isinstance(text, str) and text != text.upper():
                errors.append(f"{plate_prefix}: plate_text must be uppercase")
            if plate.get("layout", "unknown") not in valid_layouts:
                errors.append(f"{plate_prefix}: invalid layout")
            if plate.get("difficulty", "medium") not in valid_difficulties:
                errors.append(f"{plate_prefix}: invalid difficulty")
    return errors


def _valid_bbox(value: Any, image_width: int, image_height: int) -> bool:
    if not isinstance(value, list) or len(value) != 4:
        return False
    if any(
        isinstance(item, bool)
        or not isinstance(item, (int, float))
        or not math.isfinite(item)
        for item in value
    ):
        return False
    x, y, width, height = value
    return (
        x >= 0
        and y >= 0
        and width > 0
        and height > 0
        and x + width <= image_width
        and y + height <= image_height
    )
