from pathlib import Path


def ensure_storage_layout(storage_root: Path) -> None:
    for directory in ("uploads", "jobs", "exports"):
        (storage_root / directory).mkdir(parents=True, exist_ok=True)

