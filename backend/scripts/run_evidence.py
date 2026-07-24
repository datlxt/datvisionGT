import argparse
import json
from dataclasses import asdict
from pathlib import Path

from app.vision.media import EvidenceExtractor


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract timestamp-correct video evidence")
    parser.add_argument("source", type=Path)
    parser.add_argument("--storage-root", type=Path, default=Path("/app/storage"))
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--sample-rate", type=float, default=4.0)
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--jpeg-quality", type=int, default=92)
    args = parser.parse_args()

    manifest = EvidenceExtractor().extract(
        source_path=args.source,
        storage_root=args.storage_root,
        job_id=args.job_id,
        sample_rate=args.sample_rate,
        max_frames=args.max_frames,
        jpeg_quality=args.jpeg_quality,
    )
    summary = {
        "job_id": manifest.job_id,
        "source": asdict(manifest.source),
        "sampled_frames": len(manifest.frames),
        "decoded_frames": manifest.decoded_frame_count,
        "manifest_path": str(manifest.manifest_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
