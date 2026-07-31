"""Export a fast-plate-ocr training set from human-VERIFIED ground truth.

For every verified plate it re-extracts several plate crops across the vehicle's track
window (not just the single stored best frame), so the dataset captures the real glare /
sticker / blur variations — all carrying the human-confirmed label. Output matches the
fast-plate-ocr annotations format: an images folder plus train/val CSVs with the two
columns ``image_path`` and ``plate_text``.

Run inside the worker container (needs the models + DB):

    docker compose exec worker python -m scripts.export_ocr_trainset \
        --output-root /app/storage/datasets/vn-ocr-finetune \
        --frames-per-plate 20 --val-ratio 0.15
"""

from __future__ import annotations

import argparse
import csv
import random
from collections import defaultdict
from pathlib import Path

import cv2
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import GroundTruthRecord, ProcessingJob, Track
from app.vision.plate.fastalpr_adapter import FastAlprPlateEngine, crop_bgr

NO_PLATE = "LPN_NO_PLATE_VEHICLE"


def _plate_like(bbox: tuple[int, int, int, int]) -> bool:
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if width < 40 or height < 20:
        return False
    aspect = width / height
    return 0.9 <= aspect <= 2.6


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--frames-per-plate", type=int, default=20)
    parser.add_argument("--sample-every-ms", type=int, default=200)
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

    # Verified plates grouped by job so each source video is opened once.
    by_job: dict[str, list[tuple[str, str, int, int]]] = defaultdict(list)
    with SessionLocal() as session:
        rows = session.execute(
            select(
                Track.job_id,
                Track.track_code,
                Track.start_timestamp_ms,
                Track.end_timestamp_ms,
                GroundTruthRecord.normalized_gt_text,
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
    for _job_id, track_code, start_ms, end_ms, text, source_path in rows:
        by_job[str(source_path)].append((track_code, text, start_ms, end_ms))

    samples: list[tuple[str, str]] = []  # (relative_image_path, plate_text)
    for source_path, plates in by_job.items():
        video = (settings.storage_root / source_path).resolve()
        if not video.is_file():
            print(f"skip missing video: {video}")
            continue
        capture = cv2.VideoCapture(str(video))
        for track_code, text, start_ms, end_ms in plates:
            saved = 0
            for timestamp in range(start_ms, end_ms + 1, args.sample_every_ms):
                if saved >= args.frames_per_plate:
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
                name = f"{track_code}_{timestamp}.jpg"
                cv2.imwrite(str(images_dir / name), crop)
                samples.append((f"images/{name}", text))
                saved += 1
        capture.release()
        print(f"{source_path}: {len(plates)} plates")

    random.Random(args.seed).shuffle(samples)
    split = int(len(samples) * (1 - args.val_ratio))
    for csv_name, rows_out in (("train.csv", samples[:split]), ("val.csv", samples[split:])):
        with (args.output_root / csv_name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["image_path", "plate_text"])
            writer.writerows(rows_out)

    print(
        f"\nDONE: {len(samples)} crops from {len(by_job)} video(s) "
        f"-> train={split} val={len(samples) - split} at {args.output_root}"
    )


if __name__ == "__main__":
    main()
