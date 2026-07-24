# Lane 9 ground-truth and model export contract

Status: product contract confirmed from the user-provided workbook screenshot on 2026-07-24.
Direct workbook import is still pending because the file was open/locked and no connected Excel
session or spreadsheet artifact runtime was available.

## 1. Source of truth

The user designates this external workbook as the Lane 9 ground-truth source:

```text
D:\AICAM\Lane9_qa.xlsx
```

Do not copy or commit this workbook into the repository. It may contain real vehicle data. For
benchmarking, read it at runtime from an explicitly configured external path or import it through a
controlled GT workflow.

The screenshot shows a visible worksheet named `Plate Report`. The screenshot alone does not prove
the complete sheet list, row count, formulas, embedded-image structure or whether every `GT Plate`
cell has been filled. Those facts must be inspected when Excel is connected.

Most importantly:

- `Plate model đọc` is a prediction column.
- `GT Plate` is the ground-truth column.
- Never use `Plate model đọc` as GT merely because the workbook is called a GT file.

## 2. Requested XLSX presentation

The final model-review workbook has one visible sheet named `Plate Report` and exactly nine visible
columns:

| # | Header | Meaning | Application source |
|---:|---|---|---|
| 1 | `STT` | Sequential row number after filtering and deduplication | Generated at export time |
| 2 | `Ảnh crop biển số` | Embedded best plate crop; not a filesystem path or URL | `artifacts` through the best plate `detection.crop_artifact_id` |
| 3 | `Plate model đọc` | Normalized track-vote prediction | `recognition_results.normalized_text`; for no-plate use `LPN_NO_PLATE_VEHICLE` |
| 4 | `GT Plate` | Human-confirmed normalized plate | `ground_truth_records.normalized_gt_text`; blank while unverified |
| 5 | `Start` | Event start time in the source video | `tracks.start_timestamp_ms`, displayed as `[m]:ss` |
| 6 | `End` | Event end time in the source video | `tracks.end_timestamp_ms`, displayed as `[m]:ss` |
| 7 | `Confidence` | Multi-frame OCR vote confidence | `ground_truth_records.prediction_confidence` when present, otherwise the latest `TRACK_VOTE` recognition confidence |
| 8 | `Frame #` | Number of evidence frames supporting the row, not the absolute best-frame index | Plate detection count; for `NO_PLATE`, vehicle detection count |
| 9 | `Discard` | Human review discard state | `Có` when GT `verify_status=DISCARDED` or track status is `DISCARDED`; otherwise `Không` |

### `Frame #` clarification

The screenshot contains values such as `44`, `408`, `170` and `6`. These align with counts of
supporting detections/frames, not with absolute video frame numbers such as `2919` or `5050`.
Therefore:

```text
Frame # =
  plate_detection_count, when a plate was detected
  vehicle_detection_count, for LPN_NO_PLATE_VEHICLE
```

If the product later needs the absolute selected frame, expose it in technical metadata as
`best_frame_number`; do not silently change the meaning of the visible `Frame #` column.

## 3. Row-generation rules

1. Sort rows by `tracks.start_timestamp_ms`.
2. Export only evidence-backed vehicle events belonging to the requested job.
3. Apply temporal/near-OCR consolidation before export.
4. Apply exact normalized-plate deduplication across the job.
5. One normalized plate produces one visible row per uploaded video/job.
6. When duplicate tracks are consolidated, keep the crop with the strongest best-frame score and
   OCR agreement.
7. Preserve event time range across merged fragments: earliest start and latest end.
8. `STT` is assigned only after all filtering/deduplication.
9. A crop must belong to the same job and selected detection as the exported event.
10. Never export a row whose evidence artifact is missing or belongs to another job.

## 4. Best-frame and image rules

For recognized plates, the image in column B must:

- come from a frame whose normalized OCR agrees with the winning track vote;
- prefer high sharpness and contrast;
- penalize clipped highlights/glare and unusable exposure;
- penalize a low-confidence individual character;
- preserve the original aspect ratio;
- be embedded in the XLSX, not linked to `localhost` or a private storage path.

Recommended presentation:

- maximum image box: approximately `160 × 90 px`;
- centered in the cell;
- row height large enough to show the full crop;
- no stretching;
- light border only around the image cell.

For a confirmed no-plate vehicle:

```text
Ảnh crop biển số = Không có ảnh
Plate model đọc = LPN_NO_PLATE_VEHICLE
Confidence = —
Frame # = vehicle_detection_count
```

The vehicle/full-frame evidence still remains in the application and database even though a plate
crop cannot exist.

## 5. Prediction, GT and discard semantics

### Draft export

- `Plate model đọc`: populated from model results.
- `GT Plate`: populated only if an existing GT record contains a human/imported value; otherwise
  blank.
- `Discard`: derived from the persisted review state; never hardcoded merely to make the sheet look
  complete.

### Final GT export

A row is eligible for GT Final only when:

```text
ground_truth_records.verify_status = VERIFIED
ground_truth_records.evidence_status = VALID
ground_truth_records.is_duplicate = false
ground_truth_records.normalized_gt_text is not null
```

Discarded rows may remain in a review/draft workbook with `Discard = Có`, but they are excluded from
GT Final metrics and final training data.

## 6. Visible sheet policy

The user requested a compact review sheet. Do not put the current 20 technical CSV fields in the
primary `Plate Report` sheet.

Fields such as UUIDs, raw OCR, bounding boxes, model version, quality flags, source URLs and
pipeline version may remain:

- in the API/technical CSV;
- in an optional hidden `Metadata` worksheet; or
- in the database as audit data.

They are not visible review columns unless the user later requests them.

## 7. Current implementation gap

Implemented:

- model result API with best full-frame/vehicle/plate crop URLs;
- timestamps, best-frame number, confidences and detection counts;
- job-level duplicate consolidation;
- PostgreSQL tables for GT and review actions;
- 20-column UTF-8 technical CSV.

Not yet implemented:

- HTTP API to create/edit/verify `ground_truth_records`;
- review-action persistence from the current UI;
- XLSX exporter with embedded images;
- `Plate Report` download button;
- automatic Lane 9 GT import and metric comparison.

Do not show these missing capabilities as active.

## 8. Required implementation order

1. Add tested read/write review APIs using the existing GT tables; do not add a migration.
2. Materialize or query one GT draft row per result track.
3. Add an XLSX export service that embeds the stored crop bytes.
4. Implement the exact nine-column `Plate Report` contract.
5. Add the XLSX download endpoint and enable the UI only when it succeeds.
6. Import/match the external Lane 9 GT by timestamp and normalized plate.
7. Report event recall, false-positive rate, exact plate accuracy, character error rate and
   duplicate rate.

## 9. Acceptance checks

- The sheet contains the nine headers in the specified order.
- Each recognized row has a visible embedded crop.
- Every row resolves to stored same-job evidence.
- `GT Plate` is never copied automatically from `Plate model đọc`.
- `LPN_NO_PLATE_VEHICLE` rows are preserved.
- `Frame #` uses evidence count, not the selected absolute frame index.
- Model confidence is stored numerically and formatted as a percentage.
- Start/end values are numeric time values and displayed as `[m]:ss`.
- There are no repeated normalized plates within one job.
- No discarded/duplicate/invalid-evidence record enters GT Final.

