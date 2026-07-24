from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import urllib.request
from pathlib import Path

MODEL_ASSETS = {
    "vehicle/yolox_tiny.onnx": (
        "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/"
        "yolox_tiny.onnx"
    ),
    "plate-detector/yolo-v9-t-512-license-plates-end2end.onnx": (
        "https://github.com/ankandrew/open-image-models/releases/download/assets/"
        "yolo-v9-t-512-license-plates-end2end.onnx"
    ),
    "plate-ocr/model.onnx": (
        "https://github.com/ankandrew/fast-plate-ocr/releases/download/arg-plates/"
        "cct_xs_v2_global.onnx"
    ),
    "plate-ocr/plate_config.yaml": (
        "https://github.com/ankandrew/fast-plate-ocr/releases/download/arg-plates/"
        "cct_xs_v2_global_plate_config.yaml"
    ),
}

# Pin integrity as well as URLs. A successful HTTP response is not enough: interrupted
# GitHub asset downloads have previously left a syntactically invalid partial ONNX file.
MODEL_INTEGRITY = {
    "vehicle/yolox_tiny.onnx": {
        "size_bytes": 20_219_662,
        "sha256": "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7",
    },
    "plate-detector/yolo-v9-t-512-license-plates-end2end.onnx": {
        "size_bytes": 7_799_480,
        "sha256": "746fdd358ec110418775d7c9d8d07910d48b1a21471f92bf4421f6510d6daade",
    },
    "plate-ocr/model.onnx": {
        "size_bytes": 3_344_292,
        "sha256": "8031afb5fdc6b4d80462c9d542f1284ebd2cfddf5dbacd62609848d7e2855f44",
    },
    "plate-ocr/plate_config.yaml": {
        "size_bytes": 1_725,
        "sha256": "0335c74a305173bb6f393efed0fde03cadeaa0b649ed8e19f431016d8232d0a6",
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _matches_integrity(destination: Path, *, size_bytes: int, sha256: str) -> bool:
    return (
        destination.is_file()
        and destination.stat().st_size == size_bytes
        and sha256_file(destination) == sha256
    )


def download(
    url: str,
    destination: Path,
    *,
    force: bool,
    size_bytes: int,
    sha256: str,
) -> None:
    if not force and _matches_integrity(
        destination, size_bytes=size_bytes, sha256=sha256
    ):
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "DatVisionGT/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        if not _matches_integrity(partial, size_bytes=size_bytes, sha256=sha256):
            raise ValueError(
                f"Model integrity check failed for {url}: "
                f"expected {size_bytes} bytes and sha256={sha256}"
            )
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision pinned offline ONNX model artifacts")
    parser.add_argument("--model-root", type=Path, default=Path("models"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    root = args.model_root.resolve()
    root.mkdir(parents=True, exist_ok=True)

    checksums: dict[str, dict[str, str | int]] = {}
    for relative, url in MODEL_ASSETS.items():
        destination = root / relative
        print(f"Provisioning {relative}")
        download(url, destination, force=args.force, **MODEL_INTEGRITY[relative])
        checksums[relative] = {
            "sha256": sha256_file(destination),
            "size_bytes": destination.stat().st_size,
            "source_url": url,
        }
    manifest = root / "checksums.json"
    manifest.write_text(
        json.dumps({"schema_version": "1.0", "files": checksums}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Model checksum manifest: {manifest}")


if __name__ == "__main__":
    main()
