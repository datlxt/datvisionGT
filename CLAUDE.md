# DatVision GT — working contract for Claude

Read this file before changing the product. The detailed Lane 9 ground-truth and export contract is
in [`docs/18-lane9-gt-export-contract.md`](docs/18-lane9-gt-export-contract.md).

## Current product scope

- MVP target: motorcycles passing a toll/parking lane, rear-facing camera.
- Input: one uploaded video.
- Output unit: one evidence-backed vehicle event.
- Pipeline: YOLOX-Tiny vehicle detection, YOLOv9-T plate detection, multi-frame CCT OCR, tracking,
  best-frame selection, duplicate consolidation, PostgreSQL persistence.
- Runtime: offline ONNX Runtime CPU. No external AI API is called during video processing.
- Optional cloud OCR cross-check: an opt-in review-stage step (`POST /jobs/{id}/cross-check`) may
  send plate crops to a cloud vision model as an independent SECOND reader. It runs only after
  processing, never inside the offline worker, is disabled unless `CLOUD_OCR_ENABLED` + an API key
  are set, and never writes GT — a disagreement only flags the case (`OCR_DISAGREEMENT`) for a human.
- Current pipeline identifier: `motorcycle-alpr-v4`.

## Non-negotiable rules

1. `Prediction` is model output. It is never automatically treated as Ground Truth.
2. `GT Plate` is human-entered or imported ground truth only.
3. No evidence, no record: every exported row must resolve to its source video, timestamps, frame
   and stored crop/full-frame evidence.
4. One export row per continuous *pass* of a vehicle, not per plate string. When the tracker
   splits a single pass into fragments (lost/re-acquired track a few seconds apart), merge them
   into one row — a better later frame may replace the selected crop but must not create another
   row. But the SAME normalized plate read again after a real detection gap (default > 90s) is a
   SEPARATE pass — a vehicle that left and re-entered, or two different vehicles whose plates were
   misread to the same string — and must stay its own row. Merging across a large gap would
   silently drop a real vehicle into a false "missed" gap and lose its evidence, so it is forbidden.
   Both such occurrences are flagged `REPEATED_PLATE` (surfaced in "Cần xem lại", never
   auto-verified) so a human confirms they are genuinely distinct. The gap bound is
   `cross_plate_merge_gap_ms` in `consolidate_vehicle_events`.
5. A confirmed vehicle without a detected plate is still an event. Export its model value as
   `LPN_NO_PLATE_VEHICLE`.
6. Do not fabricate GT, review state, confidence, camera data, or export history.
7. Do not add or change database tables/columns merely to match a mockup. The existing
   `ground_truth_records`, `tracks`, `detections`, `recognition_results`, `artifacts` and `exports`
   tables are the source of truth.
8. Do not commit real GT workbooks, raw videos or evidence images to Git.

## Lane 9 reference

The user-designated GT workbook is:

```text
D:\AICAM\Lane9_qa.xlsx
```

Treat that path as an external evaluation input, not a repository asset. The workbook was open and
locked when this contract was written, and no Excel connector was available. Therefore, do not
claim its row count, formulas, full sheet list or final GT values have been programmatically
verified. The user-provided screenshot confirms a visible sheet named `Plate Report` and the
9-column presentation contract documented below.

## Required model-review Excel columns

The primary sheet must be named `Plate Report` and contain exactly these visible columns, in order:

```text
STT
Ảnh crop biển số
Plate model đọc
GT Plate
Start
End
Confidence
Frame #
Discard
```

The current implemented endpoint is still a 20-column technical CSV:

```text
GET /api/v1/jobs/{job_id}/export.csv
```

It is not the requested final XLSX review artifact. Do not relabel it as such. Before enabling an
XLSX button, implement and test the contract in `docs/18-lane9-gt-export-contract.md`, including
embedded crop images.

