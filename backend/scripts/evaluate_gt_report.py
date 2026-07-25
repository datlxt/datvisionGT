"""Evaluate a model plate report against a human QA ground-truth workbook.

Example:
    python scripts/evaluate_gt_report.py \
        --gt "D:/AICAM/Lane9_qa.xlsx" \
        --model "C:/Users/.../plate-report-lane9.xlsx"
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.benchmark.gt_report import (
    evaluate_events,
    parse_plate_report_xlsx,
    parse_qa_gt_xlsx,
    report_to_dict,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Event-level GT evaluation for plate reports")
    parser.add_argument("--gt", type=Path, required=True, help="Human QA ground-truth .xlsx")
    parser.add_argument("--model", type=Path, required=True, help="Model Plate Report .xlsx")
    args = parser.parse_args()

    gt_events = parse_qa_gt_xlsx(args.gt)
    model_events = parse_plate_report_xlsx(args.model)
    report = evaluate_events(gt_events, model_events)

    summary = report_to_dict(report)
    summary["counts"] = {"gt_events": len(gt_events), "model_events": len(model_events)}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
