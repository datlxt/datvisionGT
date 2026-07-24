from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.benchmark.dataset import load_jsonl, validate_annotations


def _iou(left: list[float], right: list[float]) -> float:
    left_x2, left_y2 = left[0] + left[2], left[1] + left[3]
    right_x2, right_y2 = right[0] + right[2], right[1] + right[3]
    intersection_width = max(0.0, min(left_x2, right_x2) - max(left[0], right[0]))
    intersection_height = max(0.0, min(left_y2, right_y2) - max(left[1], right[1]))
    intersection = intersection_width * intersection_height
    union = left[2] * left[3] + right[2] * right[3] - intersection
    return intersection / union if union > 0 else 0.0


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def evaluate_predictions(
    *,
    ground_truth_path: Path,
    predictions_path: Path,
    iou_threshold: float = 0.5,
) -> dict[str, Any]:
    if not 0 < iou_threshold <= 1:
        raise ValueError("iou_threshold must be in (0, 1]")
    annotation_errors = validate_annotations(ground_truth_path)
    if annotation_errors:
        raise ValueError("Invalid ground truth: " + "; ".join(annotation_errors[:10]))

    ground_truth = {record["image_id"]: record for record in load_jsonl(ground_truth_path)}
    predictions = {record["image_id"]: record for record in load_jsonl(predictions_path)}
    counts: defaultdict[str, int] = defaultdict(int)
    layout_counts: defaultdict[str, defaultdict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )

    for image_id, truth_record in ground_truth.items():
        truths = [plate for plate in truth_record["plates"] if not plate.get("ignore", False)]
        predicted = predictions.get(image_id, {}).get("plates", [])
        if not isinstance(predicted, list):
            raise ValueError(f"Predictions for {image_id} must contain a plates list")
        predicted = sorted(predicted, key=lambda item: item.get("confidence", 0), reverse=True)
        unmatched_truths = set(range(len(truths)))

        for candidate in predicted:
            bbox = candidate.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                raise ValueError(f"Prediction for {image_id} has an invalid bbox")
            matches = [
                (truth_index, _iou(bbox, truths[truth_index]["bbox"]))
                for truth_index in unmatched_truths
            ]
            best = max(matches, key=lambda item: item[1], default=None)
            if best is None or best[1] < iou_threshold:
                counts["false_positive"] += 1
                continue

            truth_index = best[0]
            unmatched_truths.remove(truth_index)
            truth = truths[truth_index]
            counts["true_positive"] += 1
            layout = truth.get("layout", "unknown")
            layout_counts[layout]["matched"] += 1
            if truth.get("unreadable", False):
                continue

            expected = truth.get("plate_text", "")
            actual = candidate.get("plate_text", "")
            counts["readable_ground_truth_matched"] += 1
            counts["character_errors"] += _edit_distance(expected, actual)
            counts["ground_truth_characters"] += len(expected)
            if actual == expected:
                counts["ocr_exact"] += 1
                layout_counts[layout]["ocr_exact"] += 1

        counts["false_negative"] += len(unmatched_truths)
        for truth_index in unmatched_truths:
            truth = truths[truth_index]
            if not truth.get("unreadable", False):
                counts["readable_ground_truth_missed"] += 1
            layout_counts[truth.get("layout", "unknown")]["missed"] += 1

    for image_id, prediction in predictions.items():
        if image_id not in ground_truth:
            plates = prediction.get("plates", [])
            counts["false_positive"] += len(plates) if isinstance(plates, list) else 0

    tp = counts["true_positive"]
    fp = counts["false_positive"]
    fn = counts["false_negative"]
    readable_total = (
        counts["readable_ground_truth_matched"] + counts["readable_ground_truth_missed"]
    )
    result = {
        "schema_version": "1.0",
        "iou_threshold": iou_threshold,
        "inputs": {
            "ground_truth_sha256": _sha256(ground_truth_path),
            "predictions_sha256": _sha256(predictions_path),
        },
        "detection": {
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "precision": _safe_ratio(tp, tp + fp),
            "recall": _safe_ratio(tp, tp + fn),
        },
        "recognition": {
            "readable_ground_truth": readable_total,
            "exact_matches": counts["ocr_exact"],
            "exact_accuracy_on_matched": _safe_ratio(
                counts["ocr_exact"], counts["readable_ground_truth_matched"]
            ),
            "end_to_end_exact_accuracy": _safe_ratio(counts["ocr_exact"], readable_total),
            "character_error_rate_on_matched": _safe_ratio(
                counts["character_errors"], counts["ground_truth_characters"]
            ),
        },
        "by_layout": {layout: dict(values) for layout, values in layout_counts.items()},
    }
    return result
