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
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    model_intra_op_threads: int = 4
    vehicle_model_path: str = "vehicle/yolox_tiny.onnx"
    plate_detector_model_path: str = (
        "plate-detector/yolo-v9-t-512-license-plates-end2end.onnx"
    )
    plate_ocr_model_path: str = "plate-ocr/model.onnx"
    plate_ocr_config_path: str = "plate-ocr/plate_config.yaml"
    plate_detection_threshold: float = 0.38
    orphan_plate_threshold: float = 0.60
    min_no_plate_observations: int = 5
    min_recognized_readings: int = 2

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
