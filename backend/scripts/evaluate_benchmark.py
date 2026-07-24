import argparse
import json
from pathlib import Path

from app.benchmark import evaluate_predictions, validate_annotations


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate or evaluate a plate benchmark")
    parser.add_argument("ground_truth", type=Path)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--iou", type=float, default=0.5)
    args = parser.parse_args()

    errors = validate_annotations(args.ground_truth)
    if errors:
        raise SystemExit("\n".join(errors))
    if args.predictions is None:
        print("Annotations are valid")
        return

    result = evaluate_predictions(
        ground_truth_path=args.ground_truth,
        predictions_path=args.predictions,
        iou_threshold=args.iou,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
