from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

from app.db.session import SessionLocal
from app.models import JobEvent, ProcessingJob


def mark_job_failed(
    rq_job: Any,
    _connection: Any,
    exception_type: type[BaseException],
    exception: BaseException,
    _traceback: TracebackType | None,
) -> None:
    """Mirror an RQ terminal failure into PostgreSQL for an honest UI state."""

    try:
        job_id = uuid.UUID(str(rq_job.id))
    except (TypeError, ValueError):
        return
    with SessionLocal() as session:
        job = session.get(ProcessingJob, job_id)
        if job is None or job.status in {"COMPLETED", "WAITING_FOR_REVIEW", "CANCELLED"}:
            return
        message = f"{exception_type.__name__}: {exception}"[:4000]
        job.status = "FAILED"
        job.error_code = "RQ_JOB_FAILED"
        job.error_message = message
        job.completed_at = datetime.now(UTC)
        session.add(
            JobEvent(
                job_id=job.id,
                event_type="RQ_JOB_FAILED",
                level="ERROR",
                message="Background worker failed before the pipeline could finish",
                data={"error": message},
            )
        )
        session.commit()
