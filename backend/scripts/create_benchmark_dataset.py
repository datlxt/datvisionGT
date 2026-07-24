import argparse
from pathlib import Path

from app.benchmark import create_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a benchmark dataset from evidence")
    parser.add_argument("evidence_manifest", type=Path)
    parser.add_argument("--output-root", type=Path, default=Path("/app/storage/datasets"))
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--every", type=int, default=1)
    parser.add_argument("--max-images", type=int, default=300)
    args = parser.parse_args()
    manifest = create_dataset(
        evidence_manifest_path=args.evidence_manifest,
        output_root=args.output_root,
        dataset_id=args.dataset_id,
        every_nth_frame=args.every,
        max_images=args.max_images,
    )
    print(manifest)


if __name__ == "__main__":
    main()
