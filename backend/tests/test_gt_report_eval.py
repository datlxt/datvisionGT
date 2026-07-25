from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from app.benchmark.gt_report import (
    GtEvent,
    ModelEvent,
    evaluate_events,
    normalize_plate,
    parse_plate_report_xlsx,
    parse_qa_gt_xlsx,
)


def test_normalize_plate_preserves_no_plate_sentinel() -> None:
    assert normalize_plate("29-N1 964.52") == "29N196452"
    assert normalize_plate("LPN_NO_PLATE_VEHICLE") == "LPN_NO_PLATE_VEHICLE"
    assert normalize_plate("") == ""


def test_evaluate_events_detection_and_recognition() -> None:
    gt = [
        GtEvent(1, 0, 10_000, "29N196452", "Biển đẹp"),
        GtEvent(2, 10_000, 20_000, "29X201482", "Biển đẹp"),
        GtEvent(3, 20_000, 30_000, "LPN_NO_PLATE_VEHICLE", "Xe không biển"),
        GtEvent(4, 30_000, 40_000, "27V10389", "Biển cũ, xước, mờ"),  # will be missed
    ]
    model = [
        ModelEvent(0, 10_000, "29N196452"),  # exact
        ModelEvent(10_000, 20_000, "29X201489"),  # 1-char OCR error
        ModelEvent(20_000, 30_000, "LPN_NO_PLATE_VEHICLE"),  # correct no-plate
        ModelEvent(50_000, 55_000, "99Z999999"),  # extra / false positive
    ]

    report = evaluate_events(gt, model)

    assert report.detection["true_positive"] == 3  # GT 1,2,3 matched in time
    assert report.detection["false_negative"] == 1  # GT 4 missed
    assert report.detection["false_positive"] == 1  # extra model event
    assert report.recognition["exact_matches"] == 2  # GT1 + GT3 (no-plate) correct
    # readable matched = GT1 + GT2 (GT3 is no-plate); one char wrong out of 9+9 chars.
    assert report.recognition["readable_matched"] == 2
    assert round(report.recognition["character_error_rate"], 4) == round(1 / 18, 4)
    assert len(report.missed) == 1 and report.missed[0].expected_plate == "27V10389"
    assert report.by_quality["Biển cũ, xước, mờ"] == {"total": 1, "matched": 0, "correct": 0}


def test_parse_plate_report_roundtrip(tmp_path: Path) -> None:
    from app.export import PlateReportRow, build_plate_report_workbook, workbook_to_bytes

    rows = [
        PlateReportRow("29N196452", 3_000, 13_000, 44, 0.97),
        PlateReportRow("LPN_NO_PLATE_VEHICLE", 20_000, 21_000, 6, None),
    ]
    path = tmp_path / "report.xlsx"
    path.write_bytes(workbook_to_bytes(build_plate_report_workbook(rows)))

    events = parse_plate_report_xlsx(path)
    assert [e.plate for e in events] == ["29N196452", "LPN_NO_PLATE_VEHICLE"]
    assert events[0].start_ms == 3_000 and events[0].end_ms == 13_000


def test_parse_qa_gt_layout(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["BẢNG KẾT QUẢ"])  # summary noise above the table
    sheet.append(["Pass", 26])
    sheet.append(
        ["No", "From - To", "Ảnh", "Ảnh biển số", "License Plate expected",
         "Phân loại chất lượng biển số", "Result", "Note"]
    )
    sheet.append(
        [1, "00:00 - 00:16", None, None, "29N196452", "Biển đẹp bình thường", "TP", ""]
    )
    sheet.append(
        [2, "02:17 - 02:37", None, None, "LPN_NO_PLATE_VEHICLE", "Xe không biển", "TP", ""]
    )
    path = tmp_path / "qa.xlsx"
    workbook.save(path)

    events = parse_qa_gt_xlsx(path)
    assert len(events) == 2
    assert events[0].expected_plate == "29N196452"
    assert events[0].start_ms == 0 and events[0].end_ms == 16_000
    assert events[1].expected_plate == "LPN_NO_PLATE_VEHICLE"
    assert events[0].quality == "Biển đẹp bình thường"
