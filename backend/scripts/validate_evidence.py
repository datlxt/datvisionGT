import argparse
from pathlib import Path

from app.vision.media.evidence import validate_evidence_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate evidence isolation and integrity")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--storage-root", type=Path)
    parser.add_argument("--skip-hashes", action="store_true")
    args = parser.parse_args()
    errors = validate_evidence_manifest(
        args.manifest,
        storage_root=args.storage_root,
        verify_hashes=not args.skip_hashes,
    )
    if errors:
        raise SystemExit("\n".join(errors))
    print("Evidence is valid")


if __name__ == "__main__":
    main()
