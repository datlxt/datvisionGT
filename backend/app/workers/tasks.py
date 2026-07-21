from datetime import UTC, datetime


def system_smoke_task() -> dict[str, str]:
    return {"status": "ok", "completed_at": datetime.now(UTC).isoformat()}

