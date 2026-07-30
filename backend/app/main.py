from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.condense import router as condense_router
from app.api.evidence import router as evidence_router
from app.api.ground_truth import router as ground_truth_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.results import router as results_router
from app.core.config import get_settings
from app.core.storage import ensure_storage_layout


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    ensure_storage_layout(settings.storage_root)
    yield


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health_router, prefix="/api/v1")
app.include_router(evidence_router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")
app.include_router(results_router, prefix="/api/v1")
app.include_router(ground_truth_router, prefix="/api/v1")
app.include_router(condense_router, prefix="/api/v1")


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"service": settings.app_name, "status": "running"}
