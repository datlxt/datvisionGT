from pathlib import Path

from fastapi import APIRouter, Response, status
from redis import Redis
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import engine

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


def _database_status() -> str:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return "connected"
    except Exception:
        return "unavailable"


def _redis_status(redis_url: str) -> str:
    try:
        client = Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
        return "connected" if client.ping() else "unavailable"
    except Exception:
        return "unavailable"


def _storage_status(storage_root: Path) -> str:
    return "available" if storage_root.exists() and storage_root.is_dir() else "unavailable"


@router.get("")
@router.get("/ready")
def readiness(response: Response) -> dict[str, str]:
    settings = get_settings()
    checks = {
        "database": _database_status(),
        "redis": _redis_status(settings.redis_url),
        "storage": _storage_status(settings.storage_root),
    }
    healthy = all(value in {"connected", "available"} for value in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if healthy else "degraded", **checks}

