"""Ground Truth verification service.

Turns validated model events into reviewable GT draft records and applies human
review actions (edit / verify / discard / restore / duplicate) with an audit trail.

Pure helpers at the top of this module contain the review rules and are unit-tested
without a database; the ``Session``-based functions below persist them and mirror the
``ground_truth_records`` check constraints so the API never sends the DB an illegal state.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Detection, GroundTruthRecord, ReviewAction, User
from app.vision.plate.domain import normalize_vietnamese_plate

DEFAULT_REVIEWER_EMAIL = "reviewer@datvision.local"
DEFAULT_REVIEWER_NAME = "Default Reviewer"
NO_PLATE_PREDICTION = "LPN_NO_PLATE_VEHICLE"

VERIFY_STATUSES = {"UNVERIFIED", "IN_REVIEW", "VERIFIED", "DISCARDED"}
_SNAPSHOT_FIELDS = (
    "gt_text",
    "normalized_gt_text",
    "classification",
    "verify_status",
    "evidence_status",
    "is_duplicate",
    "duplicate_of_id",
    "note",
    "version",
)


# --------------------------------------------------------------------------- #
# Pure review rules (no database).
# --------------------------------------------------------------------------- #
def normalize_gt_text(text: str | None) -> str | None:
    """Normalize reviewer plate input the same way the model output is normalized."""

    if text is None:
        return None
    normalized = normalize_vietnamese_plate(text)
    return normalized or None


def verify_blocker(*, normalized_gt_text: str | None, evidence_status: str) -> str | None:
    """Return why a record cannot be verified, or ``None`` when verification is allowed.

    Mirrors ``ck_gt_verified_complete``: a VERIFIED record must carry normalized GT text
    and valid evidence (the reviewer identity/time are attached when we apply it).
    """

    if not normalized_gt_text:
        return "Cần nhập GT Plate trước khi xác nhận"
    if evidence_status != "VALID":
        return "Evidence chưa hợp lệ, không thể xác nhận"
    return None


def draft_prediction_text(classification: str | None, normalized_plate: str | None) -> str | None:
    """Prediction text stored on a draft record; NO_PLATE events carry no plate string."""

    if classification == "NO_PLATE":
        return None
    return normalized_plate or None


# --------------------------------------------------------------------------- #
# Persistence.
# --------------------------------------------------------------------------- #
def get_or_create_default_reviewer(session: Session) -> User:
    """Single seeded reviewer identity used until real authentication ships."""

    user = session.scalar(select(User).where(User.email == DEFAULT_REVIEWER_EMAIL))
    if user is None:
        user = User(
            full_name=DEFAULT_REVIEWER_NAME,
            email=DEFAULT_REVIEWER_EMAIL,
            password_hash="!",  # unusable login until auth is implemented
            role="REVIEWER",
            is_active=True,
        )
        session.add(user)
        session.flush()
    return user


def _snapshot(record: GroundTruthRecord) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for field in _SNAPSHOT_FIELDS:
        value = getattr(record, field)
        snapshot[field] = str(value) if isinstance(value, uuid.UUID) else value
    return snapshot


def _record_action(
    session: Session,
    record: GroundTruthRecord,
    *,
    action: str,
    before: dict[str, Any],
    actor_id: uuid.UUID | None,
    note: str | None = None,
) -> None:
    session.add(
        ReviewAction(
            job_id=record.job_id,
            ground_truth_record_id=record.id,
            actor_id=actor_id,
            action=action,
            before_state=before,
            after_state=_snapshot(record),
            note=note,
        )
    )


def materialize_draft_record(
    session: Session,
    *,
    job_id: uuid.UUID,
    track_id: uuid.UUID,
    track_code: str,
    classification: str | None,
    normalized_plate: str | None,
    confidence: float | None,
    quality_flags: list[str],
) -> GroundTruthRecord | None:
    """Create a GT draft row for a surviving event, once. Human edits are never overwritten."""

    existing = session.scalar(
        select(GroundTruthRecord).where(
            GroundTruthRecord.job_id == job_id,
            GroundTruthRecord.track_id == track_id,
        )
    )
    if existing is not None:
        return existing

    detections = list(
        session.scalars(
            select(Detection).where(
                Detection.job_id == job_id, Detection.track_id == track_id
            )
        ).all()
    )
    plate = next((item for item in detections if item.object_type == "PLATE"), None)
    vehicle = next((item for item in detections if item.object_type == "VEHICLE"), None)
    selected = plate or vehicle
    if selected is None:
        return None

    record = GroundTruthRecord(
        job_id=job_id,
        track_id=track_id,
        selected_detection_id=selected.id,
        record_code=track_code,
        record_source="MODEL",
        predicted_text=draft_prediction_text(classification, normalized_plate),
        prediction_confidence=confidence,
        # classification stores the human quality label (README §19). Only the deterministic
        # no-plate case is pre-filled; the reviewer picks the rest from the taxonomy.
        classification="Xe không biển" if classification == "NO_PLATE" else None,
        quality_flags=list(quality_flags),
        verify_status="UNVERIFIED",
        evidence_status="VALID",
    )
    session.add(record)
    session.flush()
    _record_action(session, record, action="CREATE", before={}, actor_id=None)
    return record


def apply_edit(
    session: Session,
    record: GroundTruthRecord,
    *,
    gt_text: str | None = None,
    classification: str | None = None,
    note: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> GroundTruthRecord:
    before = _snapshot(record)
    if gt_text is not None:
        record.gt_text = gt_text or None
        record.normalized_gt_text = normalize_gt_text(gt_text)
        # Editing away the confirmed text must not leave an inconsistent VERIFIED row.
        if record.verify_status == "VERIFIED" and record.normalized_gt_text is None:
            record.verify_status = "UNVERIFIED"
            record.verified_by = None
            record.verified_at = None
    if classification is not None:
        record.classification = classification
    if note is not None:
        record.note = note or None
    record.version += 1
    _record_action(session, record, action="EDIT", before=before, actor_id=actor_id)
    return record


def apply_verify(
    session: Session, record: GroundTruthRecord, *, reviewer: User
) -> GroundTruthRecord:
    blocker = verify_blocker(
        normalized_gt_text=record.normalized_gt_text, evidence_status=record.evidence_status
    )
    if blocker is not None:
        raise ValueError(blocker)
    before = _snapshot(record)
    record.verify_status = "VERIFIED"
    record.verified_by = reviewer.id
    record.verified_at = datetime.now(UTC)
    record.version += 1
    _record_action(session, record, action="VERIFY", before=before, actor_id=reviewer.id)
    return record


def apply_discard(
    session: Session, record: GroundTruthRecord, *, reviewer: User
) -> GroundTruthRecord:
    before = _snapshot(record)
    record.verify_status = "DISCARDED"
    record.version += 1
    _record_action(session, record, action="DISCARD", before=before, actor_id=reviewer.id)
    return record


def apply_restore(
    session: Session, record: GroundTruthRecord, *, reviewer: User
) -> GroundTruthRecord:
    before = _snapshot(record)
    record.verify_status = "UNVERIFIED"
    record.verified_by = None
    record.verified_at = None
    record.version += 1
    _record_action(session, record, action="RESTORE", before=before, actor_id=reviewer.id)
    return record


def apply_duplicate(
    session: Session,
    record: GroundTruthRecord,
    *,
    duplicate_of_id: uuid.UUID | None,
    reviewer: User,
) -> GroundTruthRecord:
    before = _snapshot(record)
    if duplicate_of_id is None:
        record.is_duplicate = False
        record.duplicate_of_id = None
        action = "UNMARK_DUPLICATE"
    else:
        if duplicate_of_id == record.id:
            raise ValueError("Không thể đánh dấu trùng với chính nó")
        other = session.scalar(
            select(GroundTruthRecord).where(
                GroundTruthRecord.job_id == record.job_id,
                GroundTruthRecord.id == duplicate_of_id,
            )
        )
        if other is None:
            raise ValueError("Bản ghi gốc để đánh dấu trùng không tồn tại trong job")
        record.is_duplicate = True
        record.duplicate_of_id = duplicate_of_id
        action = "MARK_DUPLICATE"
    record.version += 1
    _record_action(session, record, action=action, before=before, actor_id=reviewer.id)
    return record
