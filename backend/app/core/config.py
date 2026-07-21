from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    app_name: str = "DatVision GT"
    app_env: str = "development"
    app_debug: bool = False
    log_level: str = "INFO"

    database_url: str = (
        "postgresql+psycopg://datvision:change_me_before_deploy@postgres:5432/datvision_gt"
    )
    redis_url: str = "redis://redis:6379/0"
    rq_queue: str = "datvision"

    storage_root: Path = Path("/app/storage")
    model_root: Path = Path("/app/models")
    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

