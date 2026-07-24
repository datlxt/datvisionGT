from __future__ import annotations

import io
from pathlib import Path

from openpyxl import load_workbook
from PIL import Image

from app.export import (
    PLATE_REPORT_HEADERS,
    PlateReportRow,
    build_plate_report_workbook,
    workbook_to_bytes,
)
from app.export.plate_report import format_confidence, format_timestamp


def test_timestamp_and_confidence_formatting_match_report() -> None:
    assert format_timestamp(3_000) == "0:03"
    assert format_timestamp(124_000) == "2:04"
    assert format_timestamp(-5) == "0:00"
    assert format_confidence(0.97) == "97%"
    assert format_confidence(None) == "—"


def _make_crop(path: Path) -> Path:
    Image.new("RGB", (120, 60), color=(40, 80, 160)).save(path, format="JPEG")
    return path


def test_workbook_layout_values_and_embedded_crop(tmp_path: Path) -> None:
    crop = _make_crop(tmp_path / "plate.jpg")
    rows = [
        PlateReportRow(
            plate_text="29N196452",
            start_ms=3_000,
            end_ms=13_000,
            frame_number=44,
            confidence=0.97,
            crop_path=crop,
        ),
        PlateReportRow(
            plate_text="LPN_NO_PLATE_VEHICLE",
            start_ms=139_000,
            end_ms=140_000,
            frame_number=6,
            confidence=None,
            crop_path=None,
        ),
    ]

    workbook = load_workbook(io.BytesIO(workbook_to_bytes(build_plate_report_workbook(rows))))
    sheet = workbook.active

    assert sheet.title == "Plate Report"
    assert sheet.freeze_panes == "A2"
    assert tuple(cell.value for cell in sheet[1]) == PLATE_REPORT_HEADERS

    # Recognized motorcycle row.
    assert sheet["A2"].value == 1
    assert sheet["C2"].value == "29N196452"
    assert sheet["D2"].value is None  # GT Plate stays empty for the reviewer.
    assert sheet["E2"].value == "0:03"
    assert sheet["F2"].value == "0:13"
    assert sheet["G2"].value == "97%"
    assert sheet["H2"].value == 44
    assert sheet["I2"].value == "Không"

    # NO_PLATE row keeps a placeholder instead of an image.
    assert sheet["C3"].value == "LPN_NO_PLATE_VEHICLE"
    assert sheet["G3"].value == "—"
    assert sheet["B3"].value == "Không có ảnh"

    # Exactly one crop is embedded (the recognized row only).
    assert len(sheet._images) == 1
    assert sheet.auto_filter.ref == "A1:K3"


def test_empty_report_has_only_headers() -> None:
    workbook = load_workbook(io.BytesIO(workbook_to_bytes(build_plate_report_workbook([]))))
    sheet = workbook.active
    assert sheet.max_row == 1
    assert tuple(cell.value for cell in sheet[1]) == PLATE_REPORT_HEADERS
