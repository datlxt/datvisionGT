"""Native ``Plate Report`` Excel exporter.

Reproduces the human-facing report format (one row per motorcycle event, with an
embedded plate crop and reviewer columns) directly from validated pipeline evidence,
replacing the browser userscript that scraped the legacy web table.

The module is intentionally decoupled from FastAPI and SQLAlchemy: callers pass plain
``PlateReportRow`` values plus resolved crop paths, so it can be unit-tested offline.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook
from openpyxl.drawing.image import Image as XlsxImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

SHEET_TITLE = "Plate Report"
NO_PLATE_TEXT = "LPN_NO_PLATE_VEHICLE"
MISSING_IMAGE_TEXT = "Không có ảnh"

# (header, width) pairs, kept in the exact order the reviewers already use.
_COLUMNS: tuple[tuple[str, int], ...] = (
    ("STT", 7),
    ("Ảnh crop biển số", 25),
    ("Plate model đọc", 22),
    ("GT Plate", 22),
    ("Start", 12),
    ("End", 12),
    ("Confidence", 15),
    ("Frame #", 12),
    ("Discard", 12),
    ("Kết quả QA", 20),
    ("Ghi chú QA", 40),
)
PLATE_REPORT_HEADERS: tuple[str, ...] = tuple(header for header, _ in _COLUMNS)

_QA_OPTIONS = "Đúng,Sai,Trùng,Không biển,Ảnh mờ,Cần kiểm tra"
_CROP_WIDTH_PX = 125
_CROP_HEIGHT_PX = 60
_ROW_HEIGHT_PT = 64

_HEADER_FILL = PatternFill(fill_type="solid", fgColor="D9EAF7")
_HEADER_FONT = Font(bold=True)
_HEADER_ALIGN = Alignment(vertical="center", horizontal="center", wrap_text=True)
_HEADER_BORDER = Border(*(Side(style="thin", color="808080"),) * 4)
_CELL_BORDER = Border(*(Side(style="thin", color="D9D9D9"),) * 4)
_CELL_ALIGN = Alignment(vertical="center", wrap_text=True)
_CENTER_ALIGN = Alignment(vertical="center", horizontal="center")


@dataclass(frozen=True, slots=True)
class PlateReportRow:
    """A single reviewable motorcycle event."""

    plate_text: str
    start_ms: int
    end_ms: int
    frame_number: int
    confidence: float | None = None
    gt_text: str = ""
    discard: bool = False
    qa_result: str = ""
    qa_note: str = ""
    crop_path: Path | None = None


def format_timestamp(milliseconds: int) -> str:
    """Render milliseconds as ``m:ss`` to match the existing report."""

    total_seconds = max(0, milliseconds) // 1000
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"


def format_confidence(confidence: float | None) -> str:
    return "—" if confidence is None else f"{round(confidence * 100)}%"


def build_plate_report_workbook(rows: list[PlateReportRow]) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_TITLE
    sheet.freeze_panes = "A2"

    sheet.append(list(PLATE_REPORT_HEADERS))
    header = sheet[1]
    sheet.row_dimensions[1].height = 28
    for index, (cell, (_, width)) in enumerate(zip(header, _COLUMNS, strict=True), start=1):
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = _HEADER_ALIGN
        cell.border = _HEADER_BORDER
        sheet.column_dimensions[get_column_letter(index)].width = width

    qa_validation = DataValidation(
        type="list", formula1=f'"{_QA_OPTIONS}"', allow_blank=True, showErrorMessage=True
    )
    sheet.add_data_validation(qa_validation)

    for position, row in enumerate(rows, start=1):
        excel_row = position + 1
        values = (
            position,
            None,  # crop image is anchored separately.
            row.plate_text,
            row.gt_text,
            format_timestamp(row.start_ms),
            format_timestamp(row.end_ms),
            format_confidence(row.confidence),
            row.frame_number,
            "Có" if row.discard else "Không",
            row.qa_result,
            row.qa_note,
        )
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=excel_row, column=column, value=value)
            cell.border = _CELL_BORDER
            cell.alignment = _CENTER_ALIGN if column in (1, 8) else _CELL_ALIGN
        sheet.row_dimensions[excel_row].height = _ROW_HEIGHT_PT
        qa_validation.add(sheet.cell(row=excel_row, column=10))

        if row.crop_path is not None and row.crop_path.is_file():
            image = XlsxImage(str(row.crop_path))
            image.width = _CROP_WIDTH_PX
            image.height = _CROP_HEIGHT_PX
            sheet.add_image(image, f"B{excel_row}")
        else:
            sheet.cell(row=excel_row, column=2, value=MISSING_IMAGE_TEXT)

    sheet.auto_filter.ref = f"A1:{get_column_letter(len(_COLUMNS))}{len(rows) + 1}"
    return workbook


def workbook_to_bytes(workbook: Workbook) -> bytes:
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
