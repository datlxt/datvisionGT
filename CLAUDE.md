# DatVision GT — working contract for Claude

Read this file before changing the product. The detailed Lane 9 ground-truth and export contract is
in [`docs/18-lane9-gt-export-contract.md`](docs/18-lane9-gt-export-contract.md).

## Current product scope

- Target: motorcycles, e-bikes and small cars passing a toll/parking lane, rear-facing camera. Two
  lane modes selected at job creation: **motorcycle** (YOLOX + MOG2 motion, so bikes / e-bikes /
  cargo bikes are caught) and **car** (YOLOX car/bus/truck only, no motion).
- Input: one uploaded video, ≤ **8 GB**, `.mp4/.mov/.avi/.mkv/.m4v`. `source_name` is a display-only
  label (editable in the UI, ≤ 30 chars); the on-disk file/path are never renamed.
- Output unit: one evidence-backed vehicle event.
- Optional pre-step "Cắt video" (condense, `POST /condense`): scan the source for lane activity and
  re-encode only the busy segments into one shorter clip, so long idle footage isn't processed. It
  never writes GT; you then create a job from the condensed clip (or the raw video).
- Pipeline (`motorcycle-alpr-v4`): a single **fused** pass — frame extract + YOLOX-Tiny vehicle
  detection + YOLOv9-T plate detection + multi-frame **CCT-XS** OCR + tracking + best-frame selection
  + duplicate consolidation → PostgreSQL. Runtime: offline ONNX Runtime CPU; no external AI API is
  called during video processing.
- Optional cloud OCR cross-check (opt-in, review stage, `POST /jobs/{id}/cross-check`): after
  processing, three INDEPENDENT readers read each plate crop — the local CCT + **AI-1** (GPT) +
  **AI-2** (Qwen); a fourth AI is a classification-only tie-breaker. It runs only after processing,
  never inside the offline worker, and is disabled unless `CLOUD_OCR_ENABLED` + API keys are set.
  A **2/3 majority** agreement (`OCR_AGREE`, or `OCR_UNANIMOUS` when all three match) auto-fills GT
  and auto-verifies the case; a reader split (`OCR_DISAGREEMENT`), and every `REPEATED_PLATE`,
  `SPECIAL_PLATE` or quality disagreement, are NEVER auto-verified and always go to a human.
- Optional missed-vehicle recall ("soát bỏ sót"): a background gap-scan after the cross-check re-reads
  long empty stretches to flag vehicles the pipeline may have missed. It never writes GT — it only
  refines the "nghi bỏ sót" timeline for a human to confirm and add.

## Non-negotiable rules

1. A single model's `Prediction` is never automatically treated as Ground Truth. GT is written
   automatically ONLY when ≥ 2 of the 3 independent readers (local CCT OCR + AI-1 + AI-2) agree on the
   same normalized plate (auto-verify). Reader splits, and every `REPEATED_PLATE`, `SPECIAL_PLATE` and
   quality disagreement, are never auto-verified — a human decides.
2. `GT Plate` is human-entered / imported, or filled by the ≥ 2/3 reader consensus above; never by a
   single model on its own.
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

## Excel / CSV export (current state)

The "Xuất Excel" button is implemented and wired to:

```text
GET /api/v1/jobs/{job_id}/export.xlsx        # working GT-review workbook (one row per pass)
GET /api/v1/jobs/{job_id}/export/final.xlsx  # GT Final / benchmark workbook
GET /api/v1/jobs/{job_id}/export.csv         # technical CSV
```

`export.xlsx` currently renders with the **GT Final** template (`build_gt_final_workbook`, sheet
`GT Final`) — embedded full-frame + plate-crop images, model reading, GT, recognition level,
confidence, frame, discard/QA columns. The download filename keeps Vietnamese diacritics
(RFC 5987 `filename*`) and drops the video extension.

A separate **`Plate Report`** template (`backend/app/export/plate_report.py`) — the 9→15-column
presentation with `STT · Ảnh crop biển số · Plate model đọc · GT Plate · Start · End · Confidence ·
Frame # · Discard` plus recognition-level / QA columns — exists in code but is NOT the workbook the
button serves. Wire/switch to it (and test the contract in `docs/18-lane9-gt-export-contract.md`,
including embedded crop images) if that exact Lane-9 presentation is required.

