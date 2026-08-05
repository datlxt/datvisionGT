from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import (
    Artifact,
    Detection,
    JobEvent,
    JobModel,
    ModelVersion,
    ProcessingJob,
    RecognitionResult,
    Track,
)
from app.vision.media.reader import sha256_file
from app.vision.plate.fastalpr_adapter import FastAlprPlateEngine, crop_bgr, pad_bbox
from app.vision.plate.motion_vehicle import CompositeVehicleDetector, FixedCameraMotionDetector
from app.vision.plate.pipeline import MotorcyclePlatePipeline, PipelineFrame, RearCameraConfig
from app.vision.plate.yolox_vehicle import YoloXMotorcycleDetector
from app.workers.evidence import EvidenceCancelled, process_evidence_job

# COCO class ids + evidence label per selectable vehicle type. Same YOLOX-tiny model.
_VEHICLE_COCO_CLASSES: dict[str, tuple[tuple[int, ...], str]] = {
    "motorcycle": ((3,), "motorcycle"),
    "car": ((2,), "car"),
}


def _model_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Model path escaped MODEL_ROOT")
    return path


def _get_or_create_model(
    session: Any,
    *,
    model_type: str,
    name: str,
    version: str,
    file_path: Path,
    model_root: Path,
) -> ModelVersion:
    model = session.scalar(
        select(ModelVersion).where(
            ModelVersion.model_type == model_type,
            ModelVersion.name == name,
            ModelVersion.version == version,
        )
    )
    if model is None:
        model = ModelVersion(
            model_type=model_type,
            name=name,
            version=version,
            runtime="ONNX",
            file_path=file_path.relative_to(model_root.resolve()).as_posix(),
            file_sha256=sha256_file(file_path),
            config={"providers": ["CPUExecutionProvider"]},
        )
        session.add(model)
        session.flush()
    return model


def _save_crop(
    *,
    storage_root: Path,
    job_id: uuid.UUID,
    name: str,
    bgr: np.ndarray,
    kind: str,
    frame_number: int,
    timestamp_ms: int,
) -> Artifact:
    directory = storage_root / "jobs" / str(job_id) / "crops"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.jpg"
    temporary = path.with_suffix(".jpg.part")
    Image.fromarray(bgr[:, :, ::-1]).save(temporary, format="JPEG", quality=94, optimize=True)
    temporary.replace(path)
    return Artifact(
        job_id=job_id,
        kind=kind,
        storage_key=path.relative_to(storage_root.resolve()).as_posix(),
        sha256=sha256_file(path),
        mime_type="image/jpeg",
        size_bytes=path.stat().st_size,
        width=bgr.shape[1],
        height=bgr.shape[0],
        frame_number=frame_number,
        timestamp_ms=timestamp_ms,
        metadata_={},
    )


def _event_key(job_id: uuid.UUID, start_ms: int, end_ms: int, bbox: tuple[int, ...]) -> str:
    value = f"{job_id}:{start_ms}:{end_ms}:{','.join(map(str, bbox))}"
    return hashlib.sha256(value.encode()).hexdigest()


def process_plate_job(job_id: str) -> dict[str, Any]:
    """RQ vertical slice: evidence -> motorcycle -> plate -> OCR -> track vote -> DB."""

    process_evidence_job(job_id)
    settings = get_settings()
    parsed_job_id = uuid.UUID(job_id)
    model_root = settings.model_root.resolve()

    vehicle_path = _model_path(model_root, settings.vehicle_model_path)
    detector_path = _model_path(model_root, settings.plate_detector_model_path)
    ocr_path = _model_path(model_root, settings.plate_ocr_model_path)
    ocr_config_path = _model_path(model_root, settings.plate_ocr_config_path)

    with SessionLocal() as session:
        job = session.get(ProcessingJob, parsed_job_id)
        if job is None:
            raise ValueError(f"Processing job does not exist: {job_id}")
        try:
            job.status = "PROCESSING"
            job.current_stage = "MODEL_INITIALIZATION"
            job.progress = 22.0
            session.commit()

            vehicle_model = _get_or_create_model(
                session,
                model_type="VEHICLE_DETECTOR",
                name="yolox-tiny",
                version="0.1.1rc0-coco",
                file_path=vehicle_path,
                model_root=model_root,
            )
            plate_model = _get_or_create_model(
                session,
                model_type="PLATE_DETECTOR",
                name="yolo-v9-t-512-license-plate-end2end",
                version="open-image-models-0.5.1",
                file_path=detector_path,
                model_root=model_root,
            )
            ocr_model = _get_or_create_model(
                session,
                model_type="PLATE_OCR",
                name="cct-xs-v2-global-model",
                version="fast-plate-ocr-1.1.0",
                file_path=ocr_path,
                model_root=model_root,
            )
            for model, role in (
                (vehicle_model, "VEHICLE_DETECTOR"),
                (plate_model, "PLATE_DETECTOR"),
                (ocr_model, "OCR"),
            ):
                existing_link = session.scalar(
                    select(JobModel).where(
                        JobModel.job_id == job.id,
                        JobModel.model_version_id == model.id,
                        JobModel.role == role,
                    )
                )
                if existing_link is None:
                    session.add(JobModel(job_id=job.id, model_version_id=model.id, role=role))
            session.commit()

            vehicle_type = (job.config_snapshot or {}).get("vehicle_type", "motorcycle")
            class_ids, vehicle_label = _VEHICLE_COCO_CLASSES.get(
                vehicle_type, _VEHICLE_COCO_CLASSES["motorcycle"]
            )
            semantic_vehicle_detector = YoloXMotorcycleDetector(
                vehicle_path,
                vehicle_class_ids=class_ids,
                label=vehicle_label,
                intra_op_threads=settings.model_intra_op_threads,
            )
            vehicle_detector = CompositeVehicleDetector(
                [semantic_vehicle_detector, FixedCameraMotionDetector()]
            )
            alpr = FastAlprPlateEngine(
                detector_path,
                ocr_path,
                ocr_config_path,
                detection_threshold=settings.plate_detection_threshold,
                intra_op_threads=settings.model_intra_op_threads,
            )
            pipeline = MotorcyclePlatePipeline(
                vehicle_detector,
                alpr,
                alpr,
                config=RearCameraConfig(
                    min_no_plate_observations=settings.min_no_plate_observations,
                    min_recognized_readings=settings.min_recognized_readings,
                    orphan_plate_threshold=settings.orphan_plate_threshold,
                ),
            )
            frames = list(
                session.scalars(
                    select(Artifact)
                    .where(Artifact.job_id == job.id, Artifact.kind == "FULL_FRAME")
                    .order_by(Artifact.timestamp_ms)
                ).all()
            )
            if not frames:
                raise ValueError("No evidence frames available for inference")

            def iter_frames():
                for index, artifact in enumerate(frames, start=1):
                    if index == 1 or index % 25 == 0:
                        session.refresh(job)
                        if job.cancel_requested_at is not None:
                            raise EvidenceCancelled("Inference was cancelled")
                        job.current_stage = "MOTORCYCLE_PLATE_INFERENCE"
                        job.progress = 22.0 + index / len(frames) * 70.0
                        session.commit()
                    path = (settings.storage_root / artifact.storage_key).resolve(strict=True)
                    rgb = np.asarray(Image.open(path).convert("RGB"))
                    yield PipelineFrame(
                        frame_number=int(artifact.frame_number or 0),
                        timestamp_ms=int(artifact.timestamp_ms or 0),
                        storage_key=artifact.storage_key,
                        bgr=rgb[:, :, ::-1].copy(),
                    )

            events = pipeline.run(iter_frames())
            job.current_stage = "PERSISTING_RESULTS"
            job.progress = 94.0
            session.commit()

            for event in events:
                best = event.best_observation
                frame_artifact = session.scalar(
                    select(Artifact).where(
                        Artifact.job_id == job.id,
                        Artifact.storage_key == best.full_frame_key,
                    )
                )
                if frame_artifact is None:
                    raise ValueError(f"Missing full-frame artifact: {best.full_frame_key}")
                image_path = (settings.storage_root / best.full_frame_key).resolve(strict=True)
                rgb = np.asarray(Image.open(image_path).convert("RGB"))
                bgr = rgb[:, :, ::-1].copy()
                vehicle_crop = _save_crop(
                    storage_root=settings.storage_root,
                    job_id=job.id,
                    name=f"{event.track_code.lower()}_vehicle",
                    bgr=crop_bgr(bgr, best.vehicle_bbox),
                    kind="VEHICLE_CROP",
                    frame_number=best.frame_number,
                    timestamp_ms=best.timestamp_ms,
                )
                session.add(vehicle_crop)
                session.flush()

                track = Track(
                    job_id=job.id,
                    track_code=event.track_code,
                    object_type="VEHICLE",
                    start_frame=event.start_frame,
                    end_frame=event.end_frame,
                    start_timestamp_ms=event.start_timestamp_ms,
                    end_timestamp_ms=event.end_timestamp_ms,
                    quality_score=best.quality_score,
                    classification=event.classification.value,
                    status="READY_FOR_REVIEW",
                    evidence_status="VALID",
                    event_key=_event_key(
                        job.id, event.start_timestamp_ms, event.end_timestamp_ms, best.vehicle_bbox
                    ),
                )
                session.add(track)
                session.flush()
                vehicle_detection = Detection(
                    job_id=job.id,
                    track_id=track.id,
                    object_type="VEHICLE",
                    source="MODEL",
                    frame_number=best.frame_number,
                    timestamp_ms=best.timestamp_ms,
                    bbox_x1=best.vehicle_bbox[0],
                    bbox_y1=best.vehicle_bbox[1],
                    bbox_x2=best.vehicle_bbox[2],
                    bbox_y2=best.vehicle_bbox[3],
                    detection_confidence=best.vehicle_confidence,
                    quality_score=best.quality_score,
                    is_best=best.plate_bbox is None,
                    evidence_status="VALID",
                    full_frame_artifact_id=frame_artifact.id,
                    crop_artifact_id=vehicle_crop.id,
                    model_version_id=vehicle_model.id,
                    raw_output={
                        "vehicle_label": best.vehicle_label,
                        "vehicle_detection_count": event.vehicle_detection_count,
                        "plate_detection_count": event.plate_detection_count,
                        "quality_flags": list(event.quality_flags),
                    },
                )
                session.add(vehicle_detection)

                if best.plate_bbox is not None:
                    plate_crop = _save_crop(
                        storage_root=settings.storage_root,
                        job_id=job.id,
                        name=f"{event.track_code.lower()}_plate",
                        bgr=crop_bgr(bgr, pad_bbox(best.plate_bbox, bgr.shape)),
                        kind="PLATE_CROP",
                        frame_number=best.frame_number,
                        timestamp_ms=best.timestamp_ms,
                    )
                    session.add(plate_crop)
                    session.flush()
                    plate_detection = Detection(
                        job_id=job.id,
                        track_id=track.id,
                        object_type="PLATE",
                        source="MODEL",
                        frame_number=best.frame_number,
                        timestamp_ms=best.timestamp_ms,
                        bbox_x1=best.plate_bbox[0],
                        bbox_y1=best.plate_bbox[1],
                        bbox_x2=best.plate_bbox[2],
                        bbox_y2=best.plate_bbox[3],
                        detection_confidence=float(best.plate_confidence or 0),
                        quality_score=best.quality_score,
                        is_best=True,
                        evidence_status="VALID",
                        full_frame_artifact_id=frame_artifact.id,
                        crop_artifact_id=plate_crop.id,
                        model_version_id=plate_model.id,
                        raw_output={},
                    )
                    session.add(plate_detection)
                    session.flush()
                    if event.normalized_plate and event.raw_plate and event.confidence is not None:
                        session.add(
                            RecognitionResult(
                                job_id=job.id,
                                track_id=track.id,
                                detection_id=plate_detection.id,
                                stage="TRACK_VOTE",
                                predicted_text=event.raw_plate,
                                normalized_text=event.normalized_plate,
                                confidence=event.confidence,
                                model_version_id=ocr_model.id,
                                candidates=[],
                                raw_output={"method": "confidence_quality_weighted_vote"},
                            )
                        )

            job.status = "WAITING_FOR_REVIEW"
            job.current_stage = "RESULTS_READY"
            job.progress = 100.0
            job.completed_at = datetime.now(UTC)
            session.add(
                JobEvent(
                    job_id=job.id,
                    event_type="RESULTS_READY",
                    message="Validated motorcycle events are ready for review and export",
                    data={
                        "event_count": len(events),
                        "no_plate_count": sum(
                            event.classification.value == "NO_PLATE" for event in events
                        ),
                    },
                )
            )
            session.commit()
            # Kick off the cloud OCR cross-check as a SEPARATE background job so the reviewer opens
            # the case with AI second-opinions already attached — without slowing or blocking the
            # offline pipeline. No-op when cloud readers aren't configured; never fails the job.
            if settings.cloud_ocr_available:
                try:
                    from redis import Redis
                    from rq import Queue

                    Queue(
                        settings.rq_queue, connection=Redis.from_url(settings.redis_url)
                    ).enqueue(
                        "app.workers.cross_check.cross_check_job",
                        job_id,
                        job_timeout="1h",
                        result_ttl=86400,
                    )
                except Exception:
                    pass
            return {"job_id": job_id, "status": job.status, "event_count": len(events)}
        except Exception as exc:
            session.rollback()
            failed = session.get(ProcessingJob, parsed_job_id)
            if failed is not None:
                cancelled = isinstance(exc, EvidenceCancelled)
                failed.status = "CANCELLED" if cancelled else "FAILED"
                failed.error_code = None if cancelled else "PLATE_PIPELINE_FAILED"
                failed.error_message = None if cancelled else str(exc)[:4000]
                failed.current_stage = "MODEL_INFERENCE"
                session.add(
                    JobEvent(
                        job_id=failed.id,
                        event_type="PIPELINE_CANCELLED" if cancelled else "PIPELINE_FAILED",
                        level="WARNING" if cancelled else "ERROR",
                        message="Pipeline cancelled" if cancelled else "Pipeline failed",
                        data={"error": str(exc)[:1000]},
                    )
                )
                session.commit()
            raise
