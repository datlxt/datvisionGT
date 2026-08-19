from __future__ import annotations

import csv
import io
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.export import (
    GtFinalRow,
    PlateReportRow,
    build_gt_final_workbook,
    workbook_to_bytes,
)
from app.models import (
    Artifact,
    Detection,
    GroundTruthRecord,
    ProcessingJob,
    RecognitionResult,
    Track,
)
from app.workers.cross_check import run_cross_check

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

router = APIRouter(prefix="/jobs", tags=["results"])


class EventResult(BaseModel):
    track_id: uuid.UUID
    track_code: str
    classification: str
    normalized_plate: str | None
    raw_plate: str | None
    confidence: float | None
    start_timestamp_ms: int
    end_timestamp_ms: int
    best_timestamp_ms: int
    best_frame_number: int
    vehicle_bbox: tuple[int, int, int, int]
    plate_bbox: tuple[int, int, int, int] | None
    vehicle_confidence: float
    plate_confidence: float | None
    vehicle_detection_count: int
    plate_detection_count: int
    quality_score: float | None
    quality_flags: list[str]
    full_frame_url: str
    vehicle_crop_url: str
    plate_crop_url: str | None
    # Second-opinion reads from the cloud cross-check (None until it has been run).
    cloud_plate: str | None = None  # AI-1 (GPT)
    cloud_quality: str | None = None
    cloud_quality_all: list[str] = []  # every AI's quality label, for the reviewer to compare
    qwen_plate: str | None = None  # AI-2
    qwen_quality: str | None = None


class ResultList(BaseModel):
    job_id: uuid.UUID
    source_name: str
    status: str
    total: int
    counts: dict[str, int]
    events: list[EventResult]
    cross_check: dict[str, Any] | None = None  # background AI cross-check status/summary
    missed_scan: dict[str, Any] | None = None  # AI missed-vehicle recall (soát bỏ sót) result


def _artifact_url(job_id: uuid.UUID, artifact: Artifact) -> str:
    filename = artifact.storage_key.rsplit("/", 1)[-1]
    folder = "frames" if artifact.kind == "FULL_FRAME" else "crops"
    return f"/api/v1/evidence/{job_id}/{folder}/{filename}"


def _bbox_iou(
    left: tuple[int, int, int, int], right: tuple[int, int, int, int]
) -> float:
    intersection_width = max(0, min(left[2], right[2]) - max(left[0], right[0]))
    intersection_height = max(0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = intersection_width * intersection_height
    if intersection == 0:
        return 0.0
    left_area = max(0, left[2] - left[0]) * max(0, left[3] - left[1])
    right_area = max(0, right[2] - right[0]) * max(0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def _plate_edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def _persisted_plate_variants_match(previous: EventResult, event: EventResult) -> bool:
    if not previous.normalized_plate or not event.normalized_plate:
        return False
    if previous.normalized_plate == event.normalized_plate:
        return False
    if _plate_edit_distance(previous.normalized_plate, event.normalized_plate) > 2:
        return False
    gap_ms = event.start_timestamp_ms - previous.end_timestamp_ms
    if gap_ms <= 0:
        return True
    same_prefix = previous.normalized_plate[:2] == event.normalized_plate[:2]
    if same_prefix and gap_ms <= 500:
        return True
    return (
        same_prefix
        and gap_ms <= 1_500
        and _bbox_iou(previous.vehicle_bbox, event.vehicle_bbox) >= 0.15
    )


def _looks_like_non_plate(event: EventResult) -> bool:
    """A card/tag the rider presents at the barrier, misdetected as a plate.

    Unlike a rear plate (low on the bumper, roughly square), the card sits high in the
    frame (the rider's hand) or has a non-plate aspect ratio. Only unreadable detections
    are judged — a real plate that OCR parsed to grammar is trusted regardless of position,
    and a genuinely dirty but real plate stays because it is low and plate-shaped.
    """

    if event.classification != "UNREADABLE" or event.plate_bbox is None:
        return False
    px1, py1, px2, py2 = event.plate_bbox
    _, vy1, _, vy2 = event.vehicle_bbox
    plate_height = py2 - py1
    vehicle_height = vy2 - vy1
    if plate_height <= 0 or vehicle_height <= 0:
        return False
    vertical_position = ((py1 + py2) / 2 - vy1) / vehicle_height
    aspect = (px2 - px1) / plate_height
    return vertical_position < 0.50 or aspect < 1.0 or aspect > 2.5


def _merge_persisted_motion_candidates(
    events: list[EventResult],
    *,
    max_gap_ms: int = 1_250,
    no_plate_gap_ms: int = 14_000,
    min_no_plate_detections: int = 5,
    cross_plate_merge_gap_ms: int = 90_000,
) -> list[EventResult]:
    """Keep API/CSV at one review case when MOG2 produced adjacent fragments."""

    reviewed_events: list[EventResult] = []
    for event in events:
        if (
            event.classification == "NO_PLATE"
            and event.vehicle_detection_count < min_no_plate_detections
        ):
            # A short no-plate pass stays a visible NO_PLATE case (contract rule #5);
            # only flag the weaker evidence so the reviewer can judge it.
            event = event.model_copy(
                update={
                    "quality_flags": sorted({*event.quality_flags, "LOW_EVIDENCE_NO_PLATE"}),
                }
            )
        reviewed_events.append(event)
    events = reviewed_events

    plated = [event for event in events if event.plate_detection_count > 0]
    events = [
        event
        for event in events
        if event.plate_detection_count > 0
        or not any(
            event.start_timestamp_ms <= plate_event.end_timestamp_ms
            and event.end_timestamp_ms >= plate_event.start_timestamp_ms
            for plate_event in plated
        )
    ]
    candidates = [
        event
        for event in events
        if "MOTION_ONLY_NO_PLATE_CANDIDATE" in event.quality_flags
    ]
    stable = [
        event
        for event in events
        if "MOTION_ONLY_NO_PLATE_CANDIDATE" not in event.quality_flags
    ]
    merged: list[EventResult] = []
    for event in sorted(candidates, key=lambda item: item.start_timestamp_ms):
        if (
            not merged
            or event.start_timestamp_ms - merged[-1].end_timestamp_ms > max_gap_ms
        ):
            merged.append(event)
            continue
        previous = merged[-1]
        best = max(
            (previous, event),
            key=lambda item: (
                item.vehicle_detection_count,
                item.quality_score or 0,
                item.vehicle_confidence,
            ),
        )
        merged[-1] = best.model_copy(
            update={
                "track_code": previous.track_code,
                "start_timestamp_ms": min(
                    previous.start_timestamp_ms, event.start_timestamp_ms
                ),
                "end_timestamp_ms": max(previous.end_timestamp_ms, event.end_timestamp_ms),
                "vehicle_detection_count": (
                    previous.vehicle_detection_count + event.vehicle_detection_count
                ),
                "quality_flags": sorted(
                    {
                        *previous.quality_flags,
                        *event.quality_flags,
                        "MERGED_MOTION_FRAGMENTS",
                    }
                ),
            }
        )
    combined = sorted([*stable, *merged], key=lambda event: event.start_timestamp_ms)
    deduplicated: list[EventResult] = []
    for event in combined:
        if event.classification != "NO_PLATE":
            deduplicated.append(event)
            continue
        match_index = next(
            (
                index
                for index in range(len(deduplicated) - 1, -1, -1)
                if deduplicated[index].classification == "NO_PLATE"
                # Merge short fragments of one no-plate pass within its presence window.
                # Time proximity is the signal; bbox IoU cannot separate same-vs-different
                # bikes here (a moving bike's fragments barely overlap; two bikes share the
                # lane spot), so a larger gap — the lane clearing — marks a new vehicle.
                and event.start_timestamp_ms - deduplicated[index].end_timestamp_ms
                <= no_plate_gap_ms
            ),
            None,
        )
        if match_index is None:
            deduplicated.append(event)
            continue
        previous = deduplicated[match_index]
        best = max(
            (previous, event),
            key=lambda item: (
                item.vehicle_detection_count,
                item.quality_score or 0,
                item.vehicle_confidence,
            ),
        )
        deduplicated[match_index] = best.model_copy(
            update={
                "track_code": previous.track_code,
                "start_timestamp_ms": min(
                    previous.start_timestamp_ms, event.start_timestamp_ms
                ),
                "end_timestamp_ms": max(previous.end_timestamp_ms, event.end_timestamp_ms),
                "vehicle_detection_count": (
                    previous.vehicle_detection_count + event.vehicle_detection_count
                ),
                "quality_flags": sorted(
                    {
                        *previous.quality_flags,
                        *event.quality_flags,
                        "MERGED_DUPLICATE_TRACK",
                    }
                ),
            }
        )
    variant_merged: list[EventResult] = []
    for event in deduplicated:
        match_index = next(
            (
                index
                for index in range(len(variant_merged) - 1, -1, -1)
                if _persisted_plate_variants_match(variant_merged[index], event)
            ),
            None,
        )
        if match_index is None:
            variant_merged.append(event)
            continue
        previous = variant_merged[match_index]
        best = max(
            (previous, event),
            key=lambda item: (
                item.classification == "RECOGNIZED",
                item.confidence or 0,
                item.quality_score or 0,
                item.plate_detection_count,
            ),
        )
        flags = {
            *previous.quality_flags,
            *event.quality_flags,
            "MERGED_NEAR_OCR_VARIANT",
        }
        if best.classification == "RECOGNIZED":
            flags.discard("SINGLE_READING_OCR")
        variant_merged[match_index] = best.model_copy(
            update={
                "track_code": previous.track_code,
                "start_timestamp_ms": min(
                    previous.start_timestamp_ms, event.start_timestamp_ms
                ),
                "end_timestamp_ms": max(previous.end_timestamp_ms, event.end_timestamp_ms),
                "vehicle_detection_count": (
                    previous.vehicle_detection_count + event.vehicle_detection_count
                ),
                "plate_detection_count": (
                    previous.plate_detection_count + event.plate_detection_count
                ),
                "quality_flags": sorted(flags),
            }
        )

    # One vehicle's pass often spawns a short, weakly-read plate fragment carrying a
    # different string (89C111522 misread as 01E111573; 29C204834 as 29A104114). Edit
    # distance can't bridge those, but a brief low-evidence plate fragment that OVERLAPS
    # in time a stronger plated event is the same physical bike — it cannot be in two
    # places at once — so the misread fragment is dropped and the good read kept.
    plated_events = [event for event in variant_merged if event.plate_detection_count > 0]
    variant_merged = [
        event
        for event in variant_merged
        if not (
            event.plate_detection_count <= 3
            and any(
                other is not event
                and other.plate_detection_count > event.plate_detection_count
                and other.normalized_plate != event.normalized_plate
                and (other.confidence or 0) >= (event.confidence or 0)
                # time intervals intersect (overlap), not merely containment
                and event.start_timestamp_ms <= other.end_timestamp_ms
                and event.end_timestamp_ms >= other.start_timestamp_ms
                # Drop the weak fragment when EITHER it is short (≤2s) OR the overlapping
                # event dominates by readable frames (e.g. 1 read vs 61 = same bike whose
                # entry was briefly misread). Wall-clock alone missed long thin fragments.
                and (
                    event.end_timestamp_ms - event.start_timestamp_ms <= 2000
                    or other.plate_detection_count >= event.plate_detection_count * 5
                )
                for other in plated_events
            )
        )
    ]

    # A lone plate frame the OCR could not read, sitting right before/after a readable
    # plate event (within ~1.5s), is the same bike's blurry entry/exit — the good read
    # already represents it. Drop the phantom "unreadable" case (rule: 1 bike = 1 case).
    readable_events = [event for event in variant_merged if event.normalized_plate]
    variant_merged = [
        event
        for event in variant_merged
        if not (
            event.classification == "UNREADABLE"
            and event.plate_detection_count <= 3
            and event.end_timestamp_ms - event.start_timestamp_ms <= 1500
            and any(
                event.start_timestamp_ms - readable.end_timestamp_ms <= 1500
                and readable.start_timestamp_ms - event.end_timestamp_ms <= 1500
                for readable in readable_events
            )
        )
    ]

    # Drop card/tag false positives (unreadable, geometrically not a rear plate).
    variant_merged = [event for event in variant_merged if not _looks_like_non_plate(event)]

    unique_events: list[EventResult] = []
    plate_index: dict[str, int] = {}
    for event in sorted(variant_merged, key=lambda item: item.start_timestamp_ms):
        if not event.normalized_plate:
            unique_events.append(event)
            continue
        match_index = plate_index.get(event.normalized_plate)
        # Only merge same-plate events that are close in time (one continuous pass). Two appearances
        # far apart are SEPARATE passes — e.g. a car early AND again at the end (29601NN69 at 2:24 and
        # 34:49). Without this gap check they were merged into one event spanning the whole clip, so
        # the later pass vanished from the list. (Their REPEATED_PLATE flag already comes from the
        # pipeline consolidation; we add nothing here to keep the returned result unchanged otherwise.)
        if match_index is not None and (
            event.start_timestamp_ms - unique_events[match_index].end_timestamp_ms
            > cross_plate_merge_gap_ms
        ):
            match_index = None
        if match_index is None:
            plate_index[event.normalized_plate] = len(unique_events)
            unique_events.append(event)
            continue
        previous = unique_events[match_index]
        best = max(
            (previous, event),
            key=lambda item: (
                item.classification == "RECOGNIZED",
                # Same normalized plate ⇒ identical text, so keep the CLEAREST crop
                # (highest quality: sharp, well-exposed, least glare), not just the
                # highest OCR confidence which can come from a glary frame.
                item.quality_score or 0,
                item.confidence or 0,
                item.plate_detection_count,
            ),
        )
        flags = {
            *previous.quality_flags,
            *event.quality_flags,
            "DEDUPLICATED_BY_PLATE",
        }
        classification = best.classification
        combined_plate_count = previous.plate_detection_count + event.plate_detection_count
        if combined_plate_count >= 2 and (best.confidence or 0) >= 0.75:
            classification = "RECOGNIZED"
            flags.discard("SINGLE_READING_OCR")
        unique_events[match_index] = best.model_copy(
            update={
                "track_code": previous.track_code,
                "classification": classification,
                "start_timestamp_ms": min(
                    previous.start_timestamp_ms, event.start_timestamp_ms
                ),
                "end_timestamp_ms": max(previous.end_timestamp_ms, event.end_timestamp_ms),
                "vehicle_detection_count": (
                    previous.vehicle_detection_count + event.vehicle_detection_count
                ),
                "plate_detection_count": combined_plate_count,
                "quality_flags": sorted(flags),
            }
        )
    return unique_events


def _load_results(job_id: uuid.UUID, session: Session) -> tuple[ProcessingJob, list[EventResult]]:
    job = session.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    tracks = list(
        session.scalars(
            select(Track)
            .where(Track.job_id == job.id, Track.object_type == "VEHICLE")
            .order_by(Track.start_timestamp_ms)
        ).all()
    )
    events: list[EventResult] = []
    for track in tracks:
        detections = list(
            session.scalars(
                select(Detection).where(Detection.job_id == job.id, Detection.track_id == track.id)
            ).all()
        )
        vehicle = next((item for item in detections if item.object_type == "VEHICLE"), None)
        plate = next((item for item in detections if item.object_type == "PLATE"), None)
        if vehicle is None:
            continue
        recognition = session.scalar(
            select(RecognitionResult)
            .where(
                RecognitionResult.job_id == job.id,
                RecognitionResult.track_id == track.id,
                RecognitionResult.stage == "TRACK_VOTE",
            )
            .order_by(RecognitionResult.created_at.desc())
        )
        selected = plate or vehicle
        full_frame = session.get(Artifact, selected.full_frame_artifact_id)
        vehicle_crop = session.get(Artifact, vehicle.crop_artifact_id)
        plate_crop = session.get(Artifact, plate.crop_artifact_id) if plate else None
        if full_frame is None or vehicle_crop is None:
            continue
        raw = vehicle.raw_output or {}
        events.append(
            EventResult(
                track_id=track.id,
                track_code=track.track_code,
                classification=track.classification or "UNREADABLE",
                normalized_plate=recognition.normalized_text if recognition else None,
                raw_plate=recognition.predicted_text if recognition else None,
                confidence=recognition.confidence if recognition else None,
                start_timestamp_ms=track.start_timestamp_ms,
                end_timestamp_ms=track.end_timestamp_ms,
                best_timestamp_ms=selected.timestamp_ms,
                best_frame_number=selected.frame_number,
                vehicle_bbox=(
                    vehicle.bbox_x1,
                    vehicle.bbox_y1,
                    vehicle.bbox_x2,
                    vehicle.bbox_y2,
                ),
                plate_bbox=(
                    (plate.bbox_x1, plate.bbox_y1, plate.bbox_x2, plate.bbox_y2) if plate else None
                ),
                vehicle_confidence=vehicle.detection_confidence,
                plate_confidence=plate.detection_confidence if plate else None,
                vehicle_detection_count=int(raw.get("vehicle_detection_count", 1)),
                plate_detection_count=int(raw.get("plate_detection_count", 0)),
                quality_score=selected.quality_score,
                quality_flags=list(raw.get("quality_flags", [])),
                cloud_plate=raw.get("cloud_plate"),
                cloud_quality=raw.get("cloud_quality"),
                cloud_quality_all=list(raw.get("cloud_quality_all", [])),
                qwen_plate=raw.get("qwen_plate"),
                qwen_quality=raw.get("qwen_quality"),
                full_frame_url=_artifact_url(job.id, full_frame),
                vehicle_crop_url=_artifact_url(job.id, vehicle_crop),
                plate_crop_url=_artifact_url(job.id, plate_crop) if plate_crop else None,
            )
        )
    postprocess = (job.config_snapshot or {}).get("postprocess")
    return job, _merge_persisted_motion_candidates(events) if postprocess else events


@router.get("/{job_id}/results", response_model=ResultList)
def get_results(job_id: uuid.UUID, session: Annotated[Session, Depends(get_db)]) -> ResultList:
    job, events = _load_results(job_id, session)
    counts = {name: 0 for name in ("RECOGNIZED", "LOW_CONFIDENCE", "UNREADABLE", "NO_PLATE")}
    for event in events:
        counts[event.classification] = counts.get(event.classification, 0) + 1
    return ResultList(
        job_id=job.id,
        source_name=job.source_name,
        status=job.status,
        total=len(events),
        counts=counts,
        events=events,
        cross_check=(job.config_snapshot or {}).get("cross_check"),
        missed_scan=(job.config_snapshot or {}).get("missed_scan"),
    )


@router.get("/{job_id}/export/final.xlsx")
def export_gt_final(
    job_id: uuid.UUID, session: Annotated[Session, Depends(get_db)]
) -> StreamingResponse:
    """GT Final: only human-VERIFIED, evidence-valid, non-duplicate cases, GT text filled."""

    job, events = _load_results(job_id, session)
    records = {
        record.track_id: record
        for record in session.scalars(
            select(GroundTruthRecord).where(GroundTruthRecord.job_id == job.id)
        ).all()
    }
    rows: list[GtFinalRow] = []
    for event in events:
        record = records.get(event.track_id)
        if (
            record is None
            or record.verify_status != "VERIFIED"
            or record.evidence_status != "VALID"
            or record.is_duplicate
        ):
            continue
        rows.append(
            GtFinalRow(
                gt_text=record.normalized_gt_text or record.gt_text or "",
                classification=event.classification,
                start_ms=event.start_timestamp_ms,
                end_ms=event.end_timestamp_ms,
                quality=record.classification or "",
                full_frame_path=_evidence_path(job.id, "frames", event.full_frame_url),
                crop_path=_evidence_path(job.id, "crops", event.plate_crop_url),
            )
        )

    payload = workbook_to_bytes(build_gt_final_workbook(rows))
    filename = f"{_path_safe(job.source_name)}_GT_FINAL.xlsx"
    return StreamingResponse(
        iter([payload]),
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{job_id}/export.csv")
def export_results_csv(
    job_id: uuid.UUID, session: Annotated[Session, Depends(get_db)]
) -> StreamingResponse:
    job, events = _load_results(job_id, session)
    output = io.StringIO()
    output.write("\ufeff")  # Excel opens UTF-8 Vietnamese text correctly.
    writer = csv.writer(output)
    writer.writerow(
        [
            "stt",
            "track_id",
            "classification",
            "normalized_plate",
            "raw_ocr",
            "start_timestamp_ms",
            "end_timestamp_ms",
            "best_timestamp_ms",
            "best_frame_number",
            "vehicle_confidence",
            "plate_detection_confidence",
            "ocr_vote_confidence",
            "vehicle_detection_count",
            "plate_detection_count",
            "quality_score",
            "quality_flags",
            "full_frame_evidence",
            "vehicle_crop_evidence",
            "plate_crop_evidence",
            "pipeline_version",
        ]
    )
    for index, event in enumerate(events, start=1):
        writer.writerow(
            [
                index,
                event.track_code,
                event.classification,
                event.normalized_plate or "",
                event.raw_plate or "",
                event.start_timestamp_ms,
                event.end_timestamp_ms,
                event.best_timestamp_ms,
                event.best_frame_number,
                event.vehicle_confidence,
                event.plate_confidence or "",
                event.confidence or "",
                event.vehicle_detection_count,
                event.plate_detection_count,
                event.quality_score or "",
                "|".join(event.quality_flags),
                event.full_frame_url,
                event.vehicle_crop_url,
                event.plate_crop_url or "",
                job.pipeline_version or "motorcycle-alpr-v4",
            ]
        )
    filename = f"{_path_safe(job.source_name)}_motorcycle_events.csv"
    return StreamingResponse(
        iter([output.getvalue().encode("utf-8")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _evidence_path(job_id: uuid.UUID, folder: str, url: str | None) -> Path | None:
    """Resolve a served evidence URL back to its on-disk, job-scoped file."""

    if not url:
        return None
    settings = get_settings()
    root = settings.storage_root.resolve()
    filename = Path(url.rsplit("/", 1)[-1]).name
    candidate = (root / "jobs" / str(job_id) / folder / filename).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def _event_to_report_row(
    job_id: uuid.UUID, event: EventResult, quality: str = ""
) -> PlateReportRow:
    if event.classification == "NO_PLATE":
        plate_text = "LPN_NO_PLATE_VEHICLE"
    else:
        plate_text = event.normalized_plate or ""
    return PlateReportRow(
        plate_text=plate_text,
        start_ms=event.start_timestamp_ms,
        end_ms=event.end_timestamp_ms,
        frame_number=event.best_frame_number,
        confidence=event.confidence,
        crop_path=_evidence_path(job_id, "crops", event.plate_crop_url),
        track_code=event.track_code,
        classification=event.classification,
        quality=quality,
        full_frame_path=_evidence_path(job_id, "frames", event.full_frame_url),
    )


@router.get("/{job_id}/export.xlsx")
def export_results_xlsx(
    job_id: uuid.UUID, session: Annotated[Session, Depends(get_db)]
) -> StreamingResponse:
    # Working export: SAME template as GT Final (build_gt_final_workbook) for a consistent layout,
    # but includes every case NOT discarded (final keeps only verified). Discarded cases stay in the
    # UI (restorable) yet must never appear in an export. Not-yet-verified cases carry the model read
    # as a provisional "expected" so the sheet is usable mid-review.
    job, events = _load_results(job_id, session)
    records = {
        record.track_id: record
        for record in session.scalars(
            select(GroundTruthRecord).where(GroundTruthRecord.job_id == job.id)
        ).all()
    }
    rows: list[GtFinalRow] = []
    for event in events:
        record = records.get(event.track_id)
        # Out of the export: cases the reviewer DISCARDED, or ones marked a duplicate of another
        # (the final export drops these too). They stay in the UI (restorable) but are not GT.
        if record is not None and (record.verify_status == "DISCARDED" or record.is_duplicate):
            continue
        confirmed = record and (record.normalized_gt_text or record.gt_text)
        rows.append(
            GtFinalRow(
                gt_text=confirmed or event.normalized_plate or "",
                classification=event.classification,
                start_ms=event.start_timestamp_ms,
                end_ms=event.end_timestamp_ms,
                quality=(record.classification or "") if record else "",
                full_frame_path=_evidence_path(job.id, "frames", event.full_frame_url),
                crop_path=_evidence_path(job.id, "crops", event.plate_crop_url),
            )
        )
    payload = workbook_to_bytes(build_gt_final_workbook(rows))
    filename = f"{_path_safe(job.source_name)}_GT_review.xlsx"
    return StreamingResponse(
        iter([payload]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class CrossCheckSummary(BaseModel):
    checked: int
    agree: int
    disagree: int
    unverified: int
    auto_verified: int = 0


@router.post("/{job_id}/cross-check", response_model=CrossCheckSummary)
def cross_check_ocr(
    job_id: uuid.UUID, session: Annotated[Session, Depends(get_db)]
) -> CrossCheckSummary:
    """Second, independent OCR pass over every plate via the cloud model.

    For each plate the cloud read is compared with the local model's read; agreement raises
    trust, disagreement flags the case (OCR_DISAGREEMENT) so it lands in "Cần xem lại" with both
    answers shown. Kept OUT of the offline worker — this is an explicit, opt-in review step.
    """

    settings = get_settings()
    if not settings.cloud_ocr_available:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Cloud OCR chưa bật hoặc thiếu OPENAI_API_KEY.",
        )
    job = session.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")

    agree, disagree, unverified = run_cross_check(session, job, settings)
    # Fast-track the unanimous (all 3 readers agree) cases automatically. Lazy import avoids a
    # module-load cycle (ground_truth imports from this module).
    from app.api.ground_truth import auto_verify_unanimous

    auto_verified = auto_verify_unanimous(job.id, session)
    from app.workers.cross_check import set_cross_check_status

    set_cross_check_status(
        session,
        job,
        {
            "status": "done",
            "checked": agree + disagree + unverified,
            "agree": agree,
            "disagree": disagree,
            "unverified": unverified,
            "auto_verified": auto_verified,
        },
    )
    return CrossCheckSummary(
        checked=agree + disagree + unverified,
        agree=agree,
        disagree=disagree,
        unverified=unverified,
        auto_verified=auto_verified,
    )


@router.post("/{job_id}/missed-scan")
def trigger_missed_scan(
    job_id: uuid.UUID, session: Annotated[Session, Depends(get_db)]
) -> dict[str, str]:
    """Kick off the missed-vehicle recall (soát bỏ sót) as a BACKGROUND task for this job — used to
    populate the "nghi bỏ sót" bar on a job that predates the feature, without running the cross-check.
    Idempotent-ish: re-queuing just re-scans."""

    settings = get_settings()
    if not settings.cloud_ocr_available:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Cloud OCR chưa bật hoặc thiếu OPENAI_API_KEY."
        )
    job = session.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")

    from redis import Redis
    from rq import Queue

    from app.workers.missed import set_missed_status

    set_missed_status(session, job, {"status": "pending", "candidates": []})
    Queue(settings.rq_queue, connection=Redis.from_url(settings.redis_url)).enqueue(
        "app.workers.missed.missed_scan_job", str(job.id), job_timeout="1h", result_ttl=3600
    )
    return {"status": "queued"}


def _path_safe(name: str) -> str:
    stem = name.rsplit(".", 1)[0]
    return "".join(
        character if character.isalnum() or character in "-_" else "_" for character in stem
    )[:100]
