"""Retrofit the WEAK_CHARACTER quality flag onto already-processed jobs.

New jobs get this flag during pipeline finalize, but jobs processed before the change
predate it. Rather than re-run the whole (minutes-long) video pipeline, this re-OCRs each
stored plate crop and, when the weakest character stays uncertain (an occluded / glary digit
the model reads confidently-but-wrong, e.g. D->U), appends WEAK_CHARACTER to that vehicle
detection's raw_output so the review UI routes it to "Cần xem lại".

    docker compose exec worker python -m scripts.flag_weak_characters            # all jobs
    docker compose exec worker python -m scripts.flag_weak_characters <job_id>   # one job
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.artifact import Artifact
from app.models.detection import Detection
from app.models.track import Track
from app.vision.plate.fastalpr_adapter import FastAlprPlateEngine

WEAK_CHARACTER_THRESHOLD = 0.60


def main() -> None:
    settings = get_settings()
    root = settings.model_root.resolve()
    engine = FastAlprPlateEngine(
        root / settings.plate_detector_model_path,
        root / settings.plate_ocr_model_path,
        root / settings.plate_ocr_config_path,
    )
    storage_root = Path(settings.storage_root)
    job_filter = sys.argv[1] if len(sys.argv) > 1 else None

    flagged = scanned = 0
    with SessionLocal() as session:
        query = select(Track).where(Track.object_type == "VEHICLE")
        if job_filter:
            query = query.where(Track.job_id == job_filter)
        for track in session.scalars(query).all():
            detections = list(
                session.scalars(
                    select(Detection).where(Detection.track_id == track.id)
                ).all()
            )
            vehicle = next((d for d in detections if d.object_type == "VEHICLE"), None)
            plate = next((d for d in detections if d.object_type == "PLATE"), None)
            if vehicle is None or plate is None or plate.crop_artifact_id is None:
                continue
            crop_art = session.get(Artifact, plate.crop_artifact_id)
            if crop_art is None:
                continue
            image = cv2.imread(str(storage_root / crop_art.storage_key))
            if image is None:
                continue
            scanned += 1
            reading = engine.recognize(image)
            if reading is None or not reading.character_confidences:
                continue
            weakest = min(reading.character_confidences)
            raw = dict(vehicle.raw_output or {})
            flags = list(raw.get("quality_flags", []))
            if weakest < WEAK_CHARACTER_THRESHOLD and "WEAK_CHARACTER" not in flags:
                flags.append("WEAK_CHARACTER")
                raw["quality_flags"] = flags
                vehicle.raw_output = raw
                flag_modified(vehicle, "raw_output")
                flagged += 1
                print(
                    f"  {track.track_code}: {reading.raw_text} weakest={weakest:.2f} -> WEAK_CHARACTER"
                )
        session.commit()
    print(f"\nDONE: scanned {scanned} plate crops, flagged {flagged} weak-character reads")


if __name__ == "__main__":
    main()
