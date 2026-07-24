from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.results import EventResult, _load_results
from app.db.session import get_db
from app.models import GroundTruthRecord
from app.services import ground_truth as gt

router = APIRouter(tags=["ground-truth"])


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
    session.commit()

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
    session.commit()
    session.refresh(record)
    return record
