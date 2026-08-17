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
    # Raised to 8 GB so full-length car-lane clips (35-min recordings run 2-6 GB) can be uploaded.
    # Long videos still cost disk + time — cutting them into short clips ("Cắt video") stays the
    # recommended flow.
    max_upload_bytes: int = 8 * 1024 * 1024 * 1024
    # These models (YOLOX-tiny, YOLOv9-T, CCT-XS) are small — more intra-op threads only add
    # scheduling overhead and run SLOWER. Measured: 2 threads ≈ 1.5× faster than 4. The spare
    # cores are better used by running FRAMES in parallel (see the pipeline worker pool).
    model_intra_op_threads: int = 2
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

    # Cloud OCR cross-check (a SECOND, independent reader for every plate). Runs as a separate
    # post-processing step — never inside the offline video worker — so the core pipeline stays
    # deterministic and offline. Disabled until an API key is provided.
    cloud_ocr_enabled: bool = False
    cloud_ocr_timeout_s: float = 30.0
    # Reader A — OpenAI (GPT).
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_ocr_model: str = "gpt-5.6-terra"  # vision-capable model id; override via env if needed
    # Reader B — Qwen (Alibaba DashScope, OpenAI-compatible endpoint). A DIFFERENT vendor keeps
    # the two AI readers independent, so agreement is meaningful and disagreement is a real signal.
    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    qwen_ocr_model: str = "qwen-vl-max"
    # Reader C — a THIRD independent CLASSIFIER (quality only) so plate-quality has a 2/3 majority
    # to break ties, instead of an unresolvable 1-1 split on two readers. Its plate READ is ignored
    # (reading already has 3 sources: local OCR + AI-1 + AI-2). For a genuine cross-check it SHOULD
    # be a different vendor than AI-1/AI-2 (correlated same-family errors weaken the majority);
    # OpenAI-compatible endpoint, set its own key/model via env.
    reader_c_api_key: str = ""
    reader_c_base_url: str = ""  # empty → reuse the OpenAI endpoint (same proxy/account)
    reader_c_ocr_model: str = "gpt-4o"

    @property
    def cloud_ocr_available(self) -> bool:
        # The feature is on as long as it is enabled and at least ONE reader has a key.
        return self.cloud_ocr_enabled and bool(
            self.openai_api_key or self.qwen_api_key or self.reader_c_api_key
        )

    @property
    def openai_available(self) -> bool:
        return self.cloud_ocr_enabled and bool(self.openai_api_key)

    @property
    def qwen_available(self) -> bool:
        return self.cloud_ocr_enabled and bool(self.qwen_api_key)

    @property
    def reader_c_url(self) -> str:
        return self.reader_c_base_url or self.openai_base_url

    @property
    def reader_c_key(self) -> str:
        # Reuse the OpenAI key ONLY when reader C points at the OpenAI endpoint (same account, e.g.
        # a different OpenAI model as AI-3). A different vendor (custom base_url like Gemini) must
        # bring its own key — otherwise reader C stays off rather than sending the wrong key.
        if self.reader_c_api_key:
            return self.reader_c_api_key
        return self.openai_api_key if self.reader_c_url == self.openai_base_url else ""

    @property
    def reader_c_available(self) -> bool:
        return self.cloud_ocr_enabled and bool(self.reader_c_key)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
