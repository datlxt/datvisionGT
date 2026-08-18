from __future__ import annotations

import hashlib
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.results import EventResult, _load_results
from app.benchmark.gt_report import (
    NO_PLATE,
    ModelEvent,
    evaluate_events,
    parse_qa_gt_xlsx,
)
from app.db.session import get_db
from app.models import Artifact, Detection, GroundTruthRecord, ProcessingJob, Track
from app.services import ground_truth as gt

router = APIRouter(tags=["ground-truth"])


def _sync_job_review_status(session: Session, job_id: uuid.UUID) -> None:
    """Move a job WAITING_FOR_REVIEW <-> COMPLETED based on remaining unresolved records."""

    job = session.get(ProcessingJob, job_id)
    if job is None or job.status not in ("WAITING_FOR_REVIEW", "COMPLETED"):
        return
    total = session.scalar(
        select(func.count()).select_from(GroundTruthRecord).where(
            GroundTruthRecord.job_id == job_id
        )
    )
    unresolved = session.scalar(
        select(func.count()).select_from(GroundTruthRecord).where(
            GroundTruthRecord.job_id == job_id,
            GroundTruthRecord.verify_status.in_(("UNVERIFIED", "IN_REVIEW")),
        )
    )
    new_status = "COMPLETED" if total and not unresolved else "WAITING_FOR_REVIEW"
    if job.status != new_status:
        job.status = new_status


def _materialize_records(session: Session, job: ProcessingJob, events: list[EventResult]) -> None:
    current = {event.track_id for event in events}
    for event in events:
        gt.materialize_draft_record(
            session,
            job_id=job.id,
            track_id=event.track_id,
            track_code=event.track_code,
            classification=event.classification,
            normalized_plate=event.normalized_plate,
            confidence=event.confidence,
            quality_flags=event.quality_flags,
        )
    # Self-heal: drop stale model records left by an earlier (pre-dedup) consolidation that
    # no longer map to a shown case, so they don't block review completion. Manual additions
    # are kept.
    if current:
        session.execute(
            delete(GroundTruthRecord).where(
                GroundTruthRecord.job_id == job.id,
                GroundTruthRecord.record_source == "MODEL",
                GroundTruthRecord.track_id.not_in(current),
            )
        )
    _sync_job_review_status(session, job.id)
    session.commit()


class GroundTruthRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    track_id: uuid.UUID
    record_code: str
    record_source: str
    predicted_text: str | None
    prediction_confidence: float | None
    gt_text: str | None
    normalized_gt_text: str | None
    classification: str | None
    verify_status: str
    evidence_status: str
    is_duplicate: bool
    duplicate_of_id: uuid.UUID | None
    note: str | None
    quality_flags: list[str]
    version: int


class GroundTruthEvidence(BaseModel):
    record: GroundTruthRecordResponse
    event: EventResult | None


class GroundTruthList(BaseModel):
    job_id: uuid.UUID
    status: str
    total: int
    counts: dict[str, int]
    items: list[GroundTruthEvidence]


class EditRequest(BaseModel):
    gt_text: str | None = None
    classification: str | None = None
    note: str | None = None


class DuplicateRequest(BaseModel):
    duplicate_of_id: uuid.UUID | None = None


def _get_record(session: Session, record_id: uuid.UUID) -> GroundTruthRecord:
    record = session.get(GroundTruthRecord, record_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ground truth record not found")
    return record


@router.get("/jobs/{job_id}/ground-truth", response_model=GroundTruthList)
def list_ground_truth(
    job_id: uuid.UUID, session: Annotated[Session, Depends(get_db)]
) -> GroundTruthList:
    job, events = _load_results(job_id, session)
    events_by_track = {event.track_id: event for event in events}
    _materialize_records(session, job, events)

    records = list(
        session.scalars(
            select(GroundTruthRecord)
            .where(GroundTruthRecord.job_id == job.id)
            .order_by(GroundTruthRecord.created_at)
        ).all()
    )
    items = [
        GroundTruthEvidence(
            record=GroundTruthRecordResponse.model_validate(record),
            event=events_by_track.get(record.track_id),
        )
        for record in records
    ]
    counts: dict[str, int] = {name: 0 for name in gt.VERIFY_STATUSES}
    for record in records:
        counts[record.verify_status] = counts.get(record.verify_status, 0) + 1
    return GroundTruthList(
        job_id=job.id,
        status=job.status,
        total=len(records),
        counts=counts,
        items=items,
    )


@router.patch("/ground-truth/{record_id}", response_model=GroundTruthRecordResponse)
def edit_ground_truth(
    record_id: uuid.UUID,
    payload: EditRequest,
    session: Annotated[Session, Depends(get_db)],
) -> GroundTruthRecord:
    record = _get_record(session, record_id)
    reviewer = gt.get_or_create_default_reviewer(session)
    gt.apply_edit(
        session,
        record,
        gt_text=payload.gt_text,
        classification=payload.classification,
        note=payload.note,
        actor_id=reviewer.id,
    )
    _sync_job_review_status(session, record.job_id)
    session.commit()
    session.refresh(record)
    return record


@router.post("/ground-truth/{record_id}/verify", response_model=GroundTruthRecordResponse)
def verify_ground_truth(
    record_id: uuid.UUID, session: Annotated[Session, Depends(get_db)]
) -> GroundTruthRecord:
    record = _get_record(session, record_id)
    reviewer = gt.get_or_create_default_reviewer(session)
    try:
        gt.apply_verify(session, record, reviewer=reviewer)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    _sync_job_review_status(session, record.job_id)
    session.commit()
    session.refresh(record)
    return record


@router.post("/ground-truth/{record_id}/discard", response_model=GroundTruthRecordResponse)
def discard_ground_truth(
    record_id: uuid.UUID, session: Annotated[Session, Depends(get_db)]
) -> GroundTruthRecord:
    record = _get_record(session, record_id)
    reviewer = gt.get_or_create_default_reviewer(session)
    gt.apply_discard(session, record, reviewer=reviewer)
    _sync_job_review_status(session, record.job_id)
    session.commit()
    session.refresh(record)
    return record


@router.post("/ground-truth/{record_id}/restore", response_model=GroundTruthRecordResponse)
def restore_ground_truth(
    record_id: uuid.UUID, session: Annotated[Session, Depends(get_db)]
) -> GroundTruthRecord:
    record = _get_record(session, record_id)
    reviewer = gt.get_or_create_default_reviewer(session)
    gt.apply_restore(session, record, reviewer=reviewer)
    _sync_job_review_status(session, record.job_id)
    session.commit()
    session.refresh(record)
    return record


@router.post("/ground-truth/{record_id}/duplicate", response_model=GroundTruthRecordResponse)
def duplicate_ground_truth(
    record_id: uuid.UUID,
    payload: DuplicateRequest,
    session: Annotated[Session, Depends(get_db)],
) -> GroundTruthRecord:
    record = _get_record(session, record_id)
    reviewer = gt.get_or_create_default_reviewer(session)
    try:
        gt.apply_duplicate(
            session, record, duplicate_of_id=payload.duplicate_of_id, reviewer=reviewer
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    _sync_job_review_status(session, record.job_id)
    session.commit()
    session.refresh(record)
    return record


# --------------------------------------------------------------------------- #
# Import an existing GT workbook, auto-match it to the model events, and (on a
# second, explicit step) fill/verify the agreeing cases — no manual copy typing.
# --------------------------------------------------------------------------- #
class GtCaseItem(BaseModel):
    track_id: uuid.UUID | None
    track_code: str | None
    model_plate: str
    classification: str | None
    gt_plate: str | None
    quality: str | None
    agree: bool
    status: str  # "match" | "diff" | "extra" | "missed"


class GtCompareResponse(BaseModel):
    job_id: uuid.UUID
    gt_events: int
    model_events: int
    detection: dict[str, float]
    recognition: dict[str, float]
    items: list[GtCaseItem]


class GtApplyItem(BaseModel):
    track_id: uuid.UUID
    gt_text: str
    verify: bool = False


class GtApplyRequest(BaseModel):
    items: list[GtApplyItem]


class GtApplyResponse(BaseModel):
    filled: int
    verified: int


def _model_events(events: list[EventResult]) -> list[ModelEvent]:
    return [
        ModelEvent(
            start_ms=event.start_timestamp_ms,
            end_ms=event.end_timestamp_ms,
            plate=(
                NO_PLATE
                if event.classification == "NO_PLATE"
                else (event.normalized_plate or "")
            ),
            ref=str(event.track_id),
        )
        for event in events
    ]


@router.post("/jobs/{job_id}/gt-compare", response_model=GtCompareResponse)
def compare_ground_truth(
    job_id: uuid.UUID,
    file: UploadFile,
    session: Annotated[Session, Depends(get_db)],
) -> GtCompareResponse:
    """Match an uploaded GT workbook against the job's events and report the diff."""

    job, events = _load_results(job_id, session)
    _materialize_records(session, job, events)

    suffix = Path(file.filename or "gt.xlsx").suffix.lower() or ".xlsx"
    if suffix not in {".xlsx", ".xlsm"}:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Chỉ nhận file .xlsx")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file.file.read())
        tmp_path = Path(tmp.name)
    try:
        gt_events = parse_qa_gt_xlsx(tmp_path)
    except (ValueError, KeyError, OSError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"Không đọc được file GT: {exc}"
        ) from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    events_by_track = {str(event.track_id): event for event in events}
    report = evaluate_events(gt_events, _model_events(events))

    # Order every case by its position on the timeline so a "GT thiếu" (missed) row appears
    # exactly where the gap is, not dumped at the bottom.
    scored: list[tuple[int, GtCaseItem]] = []
    for pair in report.matched:
        source = events_by_track.get(pair.model.ref)
        scored.append((
            pair.gt.start_ms,
            GtCaseItem(
                track_id=uuid.UUID(pair.model.ref) if pair.model.ref else None,
                track_code=source.track_code if source else None,
                model_plate=pair.model.plate,
                classification=source.classification if source else None,
                gt_plate=pair.gt.expected_plate,
                quality=pair.gt.quality or None,
                agree=pair.plate_correct,
                status="match" if pair.plate_correct else "diff",
            ),
        ))
    for extra in report.extra:
        source = events_by_track.get(extra.ref)
        scored.append((
            extra.start_ms,
            GtCaseItem(
                track_id=uuid.UUID(extra.ref) if extra.ref else None,
                track_code=source.track_code if source else None,
                model_plate=extra.plate,
                classification=source.classification if source else None,
                gt_plate=None,
                quality=None,
                agree=False,
                status="extra",
            ),
        ))
    for missed in report.missed:
        scored.append((
            missed.start_ms,
            GtCaseItem(
                track_id=None,
                track_code=None,
                model_plate="",
                classification=None,
                gt_plate=missed.expected_plate,
                quality=missed.quality or None,
                agree=False,
                status="missed",
            ),
        ))
    items = [item for _, item in sorted(scored, key=lambda entry: entry[0])]

    return GtCompareResponse(
        job_id=job.id,
        gt_events=len(gt_events),
        model_events=len(events),
        detection=report.detection,
        recognition=report.recognition,
        items=items,
    )


@router.post("/jobs/{job_id}/gt-apply", response_model=GtApplyResponse)
def apply_ground_truth(
    job_id: uuid.UUID,
    payload: GtApplyRequest,
    session: Annotated[Session, Depends(get_db)],
) -> GtApplyResponse:
    """Fill GT text from the imported file and verify the agreeing cases in one pass."""

    reviewer = gt.get_or_create_default_reviewer(session)
    filled = verified = 0
    for item in payload.items:
        record = session.scalar(
            select(GroundTruthRecord).where(
                GroundTruthRecord.job_id == job_id,
                GroundTruthRecord.track_id == item.track_id,
            )
        )
        if record is None:
            continue
        gt.apply_edit(session, record, gt_text=item.gt_text, actor_id=reviewer.id)
        filled += 1
        if item.verify:
            try:
                gt.apply_verify(session, record, reviewer=reviewer)
                verified += 1
            except ValueError:
                pass
    _sync_job_review_status(session, job_id)
    session.commit()
    return GtApplyResponse(filled=filled, verified=verified)


class AutoVerifyRequest(BaseModel):
    min_confidence: float = 0.95


class AutoVerifyResponse(BaseModel):
    verified: int
    min_confidence: float


@router.post("/jobs/{job_id}/ground-truth/auto-verify", response_model=AutoVerifyResponse)
def auto_verify_ground_truth(
    job_id: uuid.UUID,
    payload: AutoVerifyRequest,
    session: Annotated[Session, Depends(get_db)],
) -> AutoVerifyResponse:
    """Reviewer-triggered fast-track: accept the model reading as GT and verify every
    high-confidence RECOGNIZED case in one pass. Low-confidence / unreadable / no-plate
    cases are left for a human to check (confidence is not correctness — README §4.2)."""

    job, events = _load_results(job_id, session)
    _materialize_records(session, job, events)
    reviewer = gt.get_or_create_default_reviewer(session)
    verified = 0
    for event in events:
        if (
            event.classification != "RECOGNIZED"
            or event.confidence is None
            or event.confidence < payload.min_confidence
            or not event.normalized_plate
            # Confidence is not correctness: a degraded crop, a single-frame read, or a plate
            # with one occluded/glary character can be confidently WRONG (29D misread as 29U at
            # 96%). Keep those for a human instead of auto-verifying them.
            or "SINGLE_READING_OCR" in event.quality_flags
            or "WEAK_CHARACTER" in event.quality_flags
            # The second (cloud) reader disagreed — two models can't agree, so a human decides.
            or "OCR_DISAGREEMENT" in event.quality_flags
            # Same plate appears again elsewhere — a human confirms it's a distinct pass, not a
            # duplicate/misread, before it becomes GT.
            or "REPEATED_PLATE" in event.quality_flags
            # Military/diplomatic plate the OCR wasn't trained on — never auto-verify.
            or "SPECIAL_PLATE" in event.quality_flags
            or (event.quality_score is not None and event.quality_score < 0.55)
        ):
            continue
        record = session.scalar(
            select(GroundTruthRecord).where(
                GroundTruthRecord.job_id == job.id,
                GroundTruthRecord.track_id == event.track_id,
            )
        )
        if record is None or record.verify_status == "VERIFIED":
            continue
        gt.apply_edit(session, record, gt_text=event.normalized_plate, actor_id=reviewer.id)
        record.quality_flags = sorted({*record.quality_flags, "AUTO_VERIFIED_HIGH_CONFIDENCE"})
        try:
            gt.apply_verify(session, record, reviewer=reviewer)
            verified += 1
        except ValueError:
            pass
    _sync_job_review_status(session, job.id)
    session.commit()
    return AutoVerifyResponse(verified=verified, min_confidence=payload.min_confidence)


def _consensus_plate(event: EventResult) -> str | None:
    """The plate a MAJORITY (≥2 of local + AI-1 + AI-2) read, grammar-normalized — or None if the
    three readers are split (each its own answer). Used to auto-fill the agreed value."""

    from collections import Counter

    from app.vision.plate.domain import plate_key

    keys = [
        plate_key(text)
        for text in (event.normalized_plate, event.cloud_plate, event.qwen_plate)
        if text
    ]
    if not keys:
        return None
    value, count = Counter(keys).most_common(1)[0]
    return value if count >= 2 and 2 * count > len(keys) else None


def auto_verify_unanimous(job_id: uuid.UUID, session: Session) -> int:
    """Fast-track every case where a MAJORITY of readers (≥2 of local + AI-1 + AI-2) agree on the
    plate — 2/3 is trusted, so the reviewer only has to touch the cases that are genuinely split
    (each reader a different answer). The agreed value wins even if the local model was the odd
    one out. Runs automatically after the background cross-check. Returns the count verified.

    A human can always change any auto-verified value later; the case still carries a light
    OCR_AGREE flag so it can be double-checked.
    """

    job, events = _load_results(job_id, session)
    _materialize_records(session, job, events)
    reviewer = gt.get_or_create_default_reviewer(session)
    verified = 0
    for event in events:
        consensus = _consensus_plate(event)
        if (
            "OCR_AGREE" not in event.quality_flags  # ≥2 of 3 readers agree (incl. unanimous)
            # Use the CONSENSUS (whatever ≥2 readers agree on) — not the local read specifically.
            # When the local model fails but both cloud AIs read the SAME plate, that is still a
            # trustworthy 2-reader agreement; requiring local here wrongly blocked those cases.
            or consensus is None
            # If the two AIs disagree on the plate QUALITY category, keep it in "Cần xem lại" so a
            # human picks the category — the plate is agreed but the category still needs a person.
            or "QUALITY_DISAGREEMENT" in event.quality_flags
            # A repeated plate needs a human ONLY when the read isn't unanimous. When all three
            # readers agree on the SAME plate for both far-apart passes, it is the same vehicle
            # re-entering (two different vehicles cannot share a real plate, and 3 independent
            # readers won't unanimously misread two plates to the same string) — the 90s split
            # already kept them as separate valid rows, so it is auto-verified (marked so it can be
            # spot-checked). A non-unanimous repeat still goes to a human.
            or (
                "REPEATED_PLATE" in event.quality_flags
                and "OCR_UNANIMOUS" not in event.quality_flags
            )
            # A special (military/diplomatic) plate — a human confirms those first.
            or "SPECIAL_PLATE" in event.quality_flags
        ):
            continue
        record = session.scalar(
            select(GroundTruthRecord).where(
                GroundTruthRecord.job_id == job.id,
                GroundTruthRecord.track_id == event.track_id,
            )
        )
        if record is None or record.verify_status == "VERIFIED":
            continue
        gt.apply_edit(session, record, gt_text=consensus, actor_id=reviewer.id)
        # Also attach the plate-quality category when the two AIs + local signal AGREED on it
        # (QUALITY_AGREE). On disagreement the plate is still auto-verified but the category is
        # left blank for a human — we don't guess quality when the readers conflict.
        if "QUALITY_AGREE" in event.quality_flags and event.cloud_quality:
            record.classification = event.cloud_quality
        markers = {
            "AUTO_VERIFIED_UNANIMOUS"
            if "OCR_UNANIMOUS" in event.quality_flags
            else "AUTO_VERIFIED_MAJORITY"
        }
        # Tag re-entries auto-verified from a unanimous repeated plate so the reviewer can filter
        # and spot-check them.
        if "REPEATED_PLATE" in event.quality_flags:
            markers.add("AUTO_VERIFIED_REPEATED")
        record.quality_flags = sorted({*record.quality_flags, *markers})
        try:
            gt.apply_verify(session, record, reviewer=reviewer)
            verified += 1
        except ValueError:
            pass
    _sync_job_review_status(session, job.id)
    session.commit()
    return verified


# --------------------------------------------------------------------------- #
# Add a case the model missed. Anchored to the nearest real evidence frame so
# it stays evidence-backed (contract rule #3), no synthetic data.
# --------------------------------------------------------------------------- #
class ManualCaseRequest(BaseModel):
    timestamp_ms: int
    end_timestamp_ms: int | None = None  # optional — the vehicle's exit time (window end)
    gt_text: str = ""  # empty => no-plate vehicle
    no_plate: bool = False
    note: str | None = None


@router.post("/jobs/{job_id}/ground-truth/manual", response_model=GroundTruthRecordResponse)
def add_manual_case(
    job_id: uuid.UUID,
    payload: ManualCaseRequest,
    session: Annotated[Session, Depends(get_db)],
) -> GroundTruthRecord:
    job = session.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")

    frames = list(
        session.scalars(
            select(Artifact).where(Artifact.job_id == job.id, Artifact.kind == "FULL_FRAME")
        ).all()
    )
    if not frames:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Job chưa có evidence frame")
    frame = min(frames, key=lambda item: abs((item.timestamp_ms or 0) - payload.timestamp_ms))

    reviewer = gt.get_or_create_default_reviewer(session)
    suffix = uuid.uuid4().hex[:8].upper()
    no_plate = payload.no_plate or not payload.gt_text.strip()
    width = frame.width or 1
    height = frame.height or 1
    frame_number = int(frame.frame_number or 0)
    timestamp_ms = int(frame.timestamp_ms or 0)
    # End of the vehicle's window: use the reviewer-provided exit time when it is after the anchor,
    # otherwise the case is a single moment (start == end).
    end_timestamp_ms = timestamp_ms
    if payload.end_timestamp_ms is not None and payload.end_timestamp_ms > timestamp_ms:
        end_timestamp_ms = int(payload.end_timestamp_ms)

    track = Track(
        job_id=job.id,
        track_code=f"MANUAL_{suffix}",
        object_type="VEHICLE",
        start_frame=frame_number,
        end_frame=frame_number,
        start_timestamp_ms=timestamp_ms,
        end_timestamp_ms=end_timestamp_ms,
        classification="NO_PLATE" if no_plate else "MANUAL_ADDITION",
        status="READY_FOR_REVIEW",
        evidence_status="VALID",
        event_key=hashlib.sha256(f"manual:{job.id}:{suffix}".encode()).hexdigest(),
    )
    session.add(track)
    session.flush()

    detection = Detection(
        job_id=job.id,
        track_id=track.id,
        object_type="VEHICLE",
        source="MANUAL",
        frame_number=frame_number,
        timestamp_ms=timestamp_ms,
        bbox_x1=0,
        bbox_y1=0,
        bbox_x2=width,
        bbox_y2=height,
        detection_confidence=1.0,
        is_best=True,
        evidence_status="VALID",
        full_frame_artifact_id=frame.id,
        crop_artifact_id=frame.id,
        raw_output={"manual": True},
    )
    session.add(detection)
    session.flush()

    gt_text = NO_PLATE if no_plate else payload.gt_text.strip()
    record = GroundTruthRecord(
        job_id=job.id,
        track_id=track.id,
        selected_detection_id=detection.id,
        record_code=track.track_code,
        record_source="MANUAL",
        predicted_text=None,
        prediction_confidence=None,
        gt_text=gt_text,
        normalized_gt_text=gt.normalize_gt_text(gt_text),
        classification="MANUAL_ADDITION",
        note=payload.note or None,
        quality_flags=["MANUAL_ADDITION"],
        # A human adding a missed case is asserting ground truth, so it is verified on the
        # spot and flows straight into GT Final (which only takes VERIFIED records).
        verify_status="VERIFIED",
        evidence_status="VALID",
        verified_by=reviewer.id,
        verified_at=datetime.now(UTC),
    )
    session.add(record)
    session.flush()
    gt._record_action(session, record, action="CREATE", before={}, actor_id=reviewer.id)
    _sync_job_review_status(session, record.job_id)
    session.commit()
    session.refresh(record)
    return record
