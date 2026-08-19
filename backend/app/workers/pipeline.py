from __future__ import annotations

import hashlib
import queue
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.workers import live
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
from app.vision.media.evidence import EvidenceExtractor
from app.vision.media.reader import sha256_file
from app.vision.plate.fastalpr_adapter import FastAlprPlateEngine, crop_bgr, pad_bbox
from app.vision.plate.motion_vehicle import CompositeVehicleDetector, FixedCameraMotionDetector
from app.vision.plate.pipeline import MotorcyclePlatePipeline, PipelineFrame, RearCameraConfig
from app.vision.plate.yolox_vehicle import YoloXMotorcycleDetector
from app.workers.evidence import EvidenceCancelled, _resolve_source, persist_evidence

# COCO class ids + evidence label per selectable vehicle type. Same YOLOX-tiny model.
# "motorcycle" mode also accepts COCO bicycle (1): plate-less e-bikes / scooters / bicycles are
# frequently classified as bicycle, not motorcycle — without this they get no semantic detection,
# fall back to motion-only, and are dropped as candidates (a real missed vehicle). "car" mode also
# accepts bus (5) and truck (7) so larger vehicles in a car lane aren't missed either.
_VEHICLE_COCO_CLASSES: dict[str, tuple[tuple[int, ...], str]] = {
    "motorcycle": ((1, 3), "motorcycle"),
    "car": ((2, 5, 7), "car"),
}


def _prefetch(source: Iterator[Any], buffer: int = 4) -> Iterator[Any]:
    """Run ``source`` on a PRODUCER thread so the NEXT frame decodes/encodes while the current one
    is being detected.

    In the fused loop, frame extraction (PyAV decode + JPEG encode + ndarray — ~30% of wall time)
    and detection (ONNX — ~64%) ran back-to-back on one thread. Extraction releases the GIL and uses
    different hardware than ONNX, so overlapping them hides most of the extract cost under detection.
    Order is preserved (a FIFO queue), and the source's exceptions (e.g. ``EvidenceCancelled`` on
    cancel) and its side effects (the manifest it stashes before returning) propagate to the caller.
    """

    channel: queue.Queue = queue.Queue(maxsize=buffer)
    done = object()
    error: list[BaseException] = []

    def _produce() -> None:
        try:
            for item in source:
                channel.put(("item", item))
        except BaseException as exc:  # propagate cancellation / decode errors to the consumer
            error.append(exc)
        finally:
            channel.put((done, None))

    thread = threading.Thread(target=_produce, name="frame-prefetch", daemon=True)
    thread.start()
    while True:
        kind, item = channel.get()
        if kind is done:
            break
        yield item
    thread.join()
    if error:
        raise error[0]


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
    """RQ vertical slice: FUSED evidence+detect in one pass -> track vote -> DB."""

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
            source = _resolve_source(settings.storage_root, job.source_path)
            job.status = "PROCESSING"
            job.current_stage = "MODEL_INITIALIZATION"
            job.progress = 1.0
            job.started_at = job.started_at or datetime.now(UTC)
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
            # Electric scooters / e-bikes are an unusual shape for COCO-trained YOLOX, so they
            # score low even as a bicycle — a 0.35 gate drops them (a real missed vehicle). Use a
            # lower gate for the two-wheeler mode to catch them; cars keep the stricter default.
            vehicle_conf = 0.28 if vehicle_type == "motorcycle" else 0.35
            semantic_vehicle_detector = YoloXMotorcycleDetector(
                vehicle_path,
                vehicle_class_ids=class_ids,
                label=vehicle_label,
                confidence_threshold=vehicle_conf,
                intra_op_threads=settings.model_intra_op_threads,
            )
            # MOG2 motion is a RECALL fallback for two-wheelers YOLOX misses when seen steeply from
            # above — but it fires on ANY moving blob (a motorbike, e-bike, cyclist, even a person),
            # so in the CAR lane it wrongly captures a passing motorbike as a NO_PLATE vehicle. Cars
            # are large and reliably found by YOLOX, so the car lane uses the SEMANTIC detector alone
            # (only COCO car/bus/truck) → two-wheelers stay out. The motorcycle lane keeps motion so
            # it still catches bicycles / e-bikes / cargo bikes (with or without a plate).
            if vehicle_type == "car":
                vehicle_detector = semantic_vehicle_detector
            else:
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
            job_config = job.config_snapshot or {}
            rear_config_kwargs: dict[str, Any] = dict(
                min_no_plate_observations=settings.min_no_plate_observations,
                min_recognized_readings=settings.min_recognized_readings,
                orphan_plate_threshold=settings.orphan_plate_threshold,
                lane_direction=job_config.get("lane_direction"),
                # Cars idle longer at the gate + take longer to leave-and-return, so a same-plate
                # re-read stays "one pass" a bit longer for cars (120s) than motorbikes (90s).
                cross_plate_merge_gap_ms=120_000 if vehicle_type == "car" else 90_000,
            )
            # Per-lane ROI drawn on the config screen restricts detection to this lane. Absent →
            # the RearCameraConfig default (full frame minus overlay corners).
            roi_config = job_config.get("roi")
            if isinstance(roi_config, list) and len(roi_config) == 4:
                rear_config_kwargs["roi"] = tuple(float(v) for v in roi_config)
            pipeline = MotorcyclePlatePipeline(
                vehicle_detector,
                alpr,
                alpr,
                config=RearCameraConfig(**rear_config_kwargs),
            )
            # FUSED pass: extract each sampled frame AND detect it in the SAME loop — no
            # decode→save→reload roundtrip, and boxes/ROI appear from the first frame (on_frame fires
            # per detected frame). The evidence manifest is captured from the generator's return
            # value, then its artifacts are persisted afterwards. Best-effort live — see live.py.
            live_conn = None
            try:
                live_conn = live.make_redis(settings.redis_url)
                live.clear(live_conn, str(job.id))  # drop stale preview from a previous retry
            except Exception:
                live_conn = None
            roi_norm = rear_config_kwargs.get("roi")
            manifest_holder: dict[str, Any] = {}

            def extract_progress(decoded: int, total: int | None) -> None:
                session.refresh(job)
                if job.cancel_requested_at is not None:
                    raise EvidenceCancelled("Processing was cancelled")
                job.processed_frames = decoded
                job.current_stage = "DETECTING"
                if total:
                    job.total_frames = total
                    job.progress = min(90.0, 1.0 + decoded / total * 89.0)
                session.commit()

            # Steady-cadence smoothing preview: the decode callback only RECORDS the latest frame
            # (cheap); the publisher's own thread encodes+publishes at an even rate, so the live
            # video doesn't stutter between the pipeline's decode bursts (see live.py).
            previewer = (
                live.LivePreviewPublisher(live_conn, str(job.id), roi_norm).start()
                if live_conn is not None
                else None
            )

            def preview_callback(av_frame, _decoded, _total):
                if previewer is not None:
                    previewer.update(av_frame, job.progress)

            def fused_frames():
                generator = EvidenceExtractor().iter_extract(
                    source_path=source,
                    storage_root=settings.storage_root,
                    job_id=str(job.id),
                    sample_rate=job.sample_rate,
                    progress_callback=extract_progress,
                    preview_callback=preview_callback,
                )
                while True:
                    try:
                        bgr, evidence_frame = next(generator)
                    except StopIteration as stop:
                        manifest_holder["manifest"] = stop.value
                        return
                    yield PipelineFrame(
                        frame_number=int(evidence_frame.frame_index or 0),
                        timestamp_ms=int((evidence_frame.timestamp_us or 0) // 1000),
                        storage_key=evidence_frame.storage_key,
                        bgr=bgr,
                    )

            def on_frame(frame, observations, _next_id):
                # Only hand the detection BOXES to the preview publisher — it draws them over its own
                # (single, monotonic) image stream. Publishing a detection-frame image here as well is
                # what made the video jump forward/backward, so we no longer do that.
                if previewer is not None:
                    previewer.set_boxes(live.build_boxes(frame.bgr, observations))

            # Prefetch: decode/encode the next frame on a producer thread while this one is detected.
            try:
                events = pipeline.run(_prefetch(fused_frames()), on_frame=on_frame)
            finally:
                if previewer is not None:
                    previewer.stop()
            manifest = manifest_holder.get("manifest")
            if manifest is None or not manifest.frames:
                raise ValueError("No evidence frames were extracted for inference")
            persist_evidence(session, job, manifest, settings.storage_root, source)
            if live_conn is not None:
                live.clear(live_conn, str(job.id))
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

            # Persist the local results first so the cloud cross-check can read them back.
            session.commit()
            # Fold the AI cross-check INTO the pipeline (not a separate second wait): re-read every
            # plate with the cloud readers, flag disagreements, and fast-track the unanimous cases —
            # so by the time the job reaches WAITING_FOR_REVIEW the AI second-opinions are already
            # attached and the reviewer waits only ONCE (import → done). Never fails the job;
            # skipped entirely when cloud readers aren't configured (fully offline).
            if settings.cloud_ocr_available:
                try:
                    job.current_stage = "CROSS_CHECKING"
                    job.progress = 92.0
                    session.commit()
                    from app.api.ground_truth import auto_verify_unanimous
                    from app.workers.cross_check import run_cross_check

                    run_cross_check(session, job, settings)
                    auto_verify_unanimous(job.id, session)
                except Exception:
                    session.rollback()  # cross-check must never break the job

            job.status = "WAITING_FOR_REVIEW"
            job.current_stage = "RESULTS_READY"
            job.progress = 100.0
            job.completed_at = datetime.now(UTC)
            # Missed-vehicle recall runs on its OWN background task (decoupled from the cross-check),
            # so the "nghi bỏ sót" bar fills in shortly after review opens without adding to the wait.
            if settings.cloud_ocr_available:
                try:
                    from redis import Redis
                    from rq import Queue

                    from app.workers.missed import set_missed_status

                    set_missed_status(session, job, {"status": "pending", "candidates": []})
                    Queue(
                        settings.rq_queue, connection=Redis.from_url(settings.redis_url)
                    ).enqueue(
                        "app.workers.missed.missed_scan_job",
                        str(job.id),
                        job_timeout="1h",
                        result_ttl=3600,
                    )
                except Exception:
                    session.rollback()
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
