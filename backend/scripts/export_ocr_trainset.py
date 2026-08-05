"""Export a fast-plate-ocr training set from human-VERIFIED ground truth.

For every verified plate it re-extracts several plate crops across the vehicle's track
window (not just the single stored best frame), so the dataset captures the real glare /
sticker / blur variations — all carrying the human-confirmed label. HARD plates (the reviewer
labelled them glare/cover/dirty/… or the pipeline & AI cross-check flagged them) are
OVERSAMPLED, because those are exactly the cases fine-tuning must fix; clean plates get fewer
frames so the set stays focused. The val split holds out WHOLE plates (no leakage). Output
matches the fast-plate-ocr annotations format: an images folder plus train/val CSVs with the
two columns ``image_path`` and ``plate_text``.

Run inside the worker container (needs the models + DB):

    docker compose exec worker python -m scripts.export_ocr_trainset \
        --output-root /app/storage/datasets/vn-ocr-finetune \
        --hard-frames 30 --easy-frames 8 --val-ratio 0.15
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import random
from collections import defaultdict
from pathlib import Path

import cv2
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import Detection, GroundTruthRecord, ProcessingJob, Track
from app.vision.plate.fastalpr_adapter import FastAlprPlateEngine, crop_bgr

NO_PLATE = "LPN_NO_PLATE_VEHICLE"

# A plate is "hard" (worth oversampling — these are what the model needs) when the reviewer
# labelled it as anything other than a clean plate, OR the pipeline/cross-check flagged it.
_HARD_QUALITY = {"", "Biển đẹp bình thường"}
_HARD_FLAGS = {"WEAK_CHARACTER", "OCR_DISAGREEMENT", "SINGLE_READING_OCR", "LOW_CONFIDENCE"}


def _is_hard(classification: str | None, flags: list[str]) -> bool:
    labelled_hard = bool(classification) and classification not in _HARD_QUALITY
    return labelled_hard or any(flag in _HARD_FLAGS for flag in flags)


def _plate_like(bbox: tuple[int, int, int, int]) -> bool:
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if width < 40 or height < 20:
        return False
    aspect = width / height
    return 0.9 <= aspect <= 2.6


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--hard-frames", type=int, default=30, help="crops for HARD plates")
    parser.add_argument("--easy-frames", type=int, default=8, help="crops for clean plates")
    parser.add_argument("--sample-every-ms", type=int, default=150)
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    settings = get_settings()
    root = settings.model_root.resolve()
    engine = FastAlprPlateEngine(
        root / settings.plate_detector_model_path,
        root / settings.plate_ocr_model_path,
        root / settings.plate_ocr_config_path,
    )

    images_dir = args.output_root / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    # Verified plates grouped by job so each source video is opened once. Each entry carries the
    # reviewer's quality label + the pipeline flags so we can oversample the HARD ones.
    by_job: dict[str, list[tuple[str, str, int, int, bool]]] = defaultdict(list)
    with SessionLocal() as session:
        rows = session.execute(
            select(
                Track.id,
                Track.track_code,
                Track.start_timestamp_ms,
                Track.end_timestamp_ms,
                GroundTruthRecord.normalized_gt_text,
                GroundTruthRecord.classification,
                ProcessingJob.source_path,
            )
            .join(GroundTruthRecord, GroundTruthRecord.track_id == Track.id)
            .join(ProcessingJob, ProcessingJob.id == Track.job_id)
            .where(
                GroundTruthRecord.verify_status == "VERIFIED",
                GroundTruthRecord.normalized_gt_text.isnot(None),
                GroundTruthRecord.normalized_gt_text != "",
                GroundTruthRecord.normalized_gt_text != NO_PLATE,
            )
        ).all()
        # Pull the vehicle-detection quality flags for every track in one pass.
        flags_by_track: dict[str, list[str]] = {}
        for det_track_id, raw in session.execute(
            select(Detection.track_id, Detection.raw_output).where(
                Detection.object_type == "VEHICLE"
            )
        ).all():
            flags_by_track[str(det_track_id)] = list((raw or {}).get("quality_flags", []))
    for track_id, track_code, start_ms, end_ms, text, classification, source_path in rows:
        hard = _is_hard(classification, flags_by_track.get(str(track_id), []))
        by_job[str(source_path)].append((track_code, text, start_ms, end_ms, hard))

    # Group crops by plate TEXT so the val split can hold out whole plates (no leakage: the same
    # plate must not appear in both train and val).
    per_plate: dict[str, list[tuple[str, str]]] = defaultdict(list)
    hard_count = 0
    for source_path, plates in by_job.items():
        video = (settings.storage_root / source_path).resolve()
        if not video.is_file():
            print(f"skip missing video: {video}")
            continue
        capture = cv2.VideoCapture(str(video))
        # Track codes repeat across videos (every job restarts VEHICLE_000001), so prefix the
        # file name with a per-video id — otherwise crops from different plates overwrite each
        # other and the CSV ends up pointing labels at the wrong image.
        video_id = hashlib.md5(source_path.encode()).hexdigest()[:6]
        for track_code, text, start_ms, end_ms, hard in plates:
            hard_count += hard
            target = args.hard_frames if hard else args.easy_frames
            saved = 0
            for timestamp in range(start_ms, end_ms + 1, args.sample_every_ms):
                if saved >= target:
                    break
                capture.set(cv2.CAP_PROP_POS_MSEC, timestamp)
                ok, frame = capture.read()
                if not ok:
                    continue
                detections = engine.detect(frame)
                if not detections:
                    continue
                best = max(detections, key=lambda d: d.confidence)
                if not _plate_like(best.bbox):
                    continue
                try:
                    crop = crop_bgr(frame, best.bbox)
                except ValueError:
                    continue
                name = f"{video_id}_{track_code}_{timestamp}.jpg"
                cv2.imwrite(str(images_dir / name), crop)
                per_plate[text].append((f"images/{name}", text))
                saved += 1
        capture.release()
        print(f"{source_path}: {len(plates)} plates")

    plates = list(per_plate)
    random.Random(args.seed).shuffle(plates)
    n_val = max(1, int(len(plates) * args.val_ratio)) if plates else 0
    val_plates = set(plates[:n_val])
    train = [s for p in plates if p not in val_plates for s in per_plate[p]]
    val = [s for p in val_plates for s in per_plate[p]]
    for csv_name, rows_out in (("train.csv", train), ("val.csv", val)):
        with (args.output_root / csv_name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["image_path", "plate_text"])
            writer.writerows(rows_out)

    total = len(train) + len(val)
    print(
        f"\nDONE: {total} crops from {len(plates)} plates "
        f"({hard_count} HARD oversampled) -> train={len(train)} val={len(val)} "
        f"at {args.output_root}"
    )


if __name__ == "__main__":
    main()
