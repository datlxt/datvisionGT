# Motorcycle ALPR MVP

## Product contract

Input is a rear-camera video of motorcycles passing a toll lane. Output is one row per motorcycle
event, not one row per frame and not only motorcycles with a readable plate.

Final classifications:

- `RECOGNIZED`: a plausible normalized plate wins the multi-frame vote at confidence >= 0.75.
- `LOW_CONFIDENCE`: a plate is read, but confidence or Vietnamese-format validation is weak.
- `UNREADABLE`: a plate is visible but OCR fails, or there is not enough evidence to prove no plate.
- `NO_PLATE`: the semantic motorcycle detector observes the vehicle in at least five sampled
  frames, no plate is detected, and overlapping split tracks have been merged.

Fixed-camera motion is used to avoid dropping possible events, but motion alone becomes an
`UNREADABLE` review candidate with `MOTION_ONLY_NO_PLATE_CANDIDATE`; it cannot automatically prove
`NO_PLATE`. This prevents pedestrians and moving shadows from becoming no-plate vehicles.
Semantic detections below that threshold also stay in review with
`INSUFFICIENT_NO_PLATE_EVIDENCE`; conservative review is preferable to a false automatic claim.

## Actual pipeline

```text
Raw video
  -> atomic upload + SHA-256 + PyAV metadata/PTS
  -> timestamp sampling at 4 FPS
  -> YOLOX-tiny motorcycle detection + fixed-camera motion candidates
  -> deterministic IoU/time tracking for the fixed lane
  -> YOLOv9-T-512 plate detection
  -> CCT-XS-v2 plate OCR
  -> Vietnamese-format validation + multi-frame confidence/quality vote
  -> same-plate temporal merge + motion-fragment suppression
  -> one vehicle event in PostgreSQL
  -> Results API + Excel-compatible UTF-8 CSV
```

The fixed-camera ROI excludes the camera identifier, legacy OCR text, and timestamp overlays. Those
regions must not become detections or benchmark evidence.

## Why these models

- Vehicle: YOLOX-tiny ONNX, Apache-2.0. This provides the parent motorcycle event needed for
  `NO_PLATE` without bringing PyTorch into production.
- Plate detector: `yolo-v9-t-512-license-plate-end2end` from `open-image-models`, MIT. The 512 model
  is the initial accuracy/CPU compromise; 640 is a benchmark challenger, not a default.
- OCR: `cct-xs-v2-global-model` from `fast-plate-ocr`, MIT. It is lightweight and exposes
  per-character confidence.
- Integration: FastALPR adapters, MIT. Models are passed as explicit local paths, so workers never
  download weights at runtime.
- Tracking: deterministic IoU/time tracker for the single fixed lane. ByteTrack is introduced only
  if the benchmark shows identity fragmentation or multiple simultaneous vehicles.

CRNN, LPRNet and PaddleOCR remain benchmark challengers. OpenALPR is reference-only because its
AGPL license and older stack are not appropriate for this product baseline.

## Model provisioning

Model files are runtime data and are not committed to Git.

```powershell
.\.venv\Scripts\python.exe backend\scripts\provision_models.py --model-root models
```

The command downloads pinned artifacts atomically, verifies expected byte size and SHA-256, and
writes `models/checksums.json`. Every model version is also stored with SHA-256 in PostgreSQL when
a job runs.

Expected files:

```text
models/
  vehicle/yolox_tiny.onnx
  plate-detector/yolo-v9-t-512-license-plates-end2end.onnx
  plate-ocr/model.onnx
  plate-ocr/plate_config.yaml
  checksums.json
```

## HTTP flow

1. `POST /api/v1/jobs` with the raw video body and `X-Filename` header.
2. `POST /api/v1/jobs/{id}/start` to enqueue the RQ worker.
3. Poll `GET /api/v1/jobs/{id}` for stage and progress.
4. Read `GET /api/v1/jobs/{id}/results`.
5. Download `GET /api/v1/jobs/{id}/export.csv`.
6. If a machine or worker was interrupted, call `POST /api/v1/jobs/{id}/retry`; the source video
   does not need to be uploaded again.

The CSV contains all event classes, including low-confidence, unreadable and no-plate cases. It
includes timestamps, best frame, confidences, detection counts, evidence URLs and pipeline version.

## Deployment profile for Dell Vostro 5502

- Docker Compose: Caddy + React, FastAPI, one RQ inference worker, PostgreSQL 16 and Redis 7.
- Runtime: ONNX Runtime CPU by default; `intra_op_threads=4`, `inter_op_threads=1`, sequential mode.
- Worker concurrency: one. Multiple model workers compete for the same CPU/RAM and reduce stability.
- OpenVINO: optional Linux benchmark profile. It is not the Windows default because the tested
  OpenVINO wheel failed to load its native DLL on this machine.
- MX330: do not make CUDA a deployment dependency. Its 2 GB VRAM and old stack add fragility for
  little benefit at the 4 FPS offline target.

For production, provision models before `docker compose up`, apply Alembic migrations, mount
`models/` read-only, mount `storage/` read-write, and keep PostgreSQL/Redis off public ports.

## Ground-truth Excel

The official Excel GT is used only after inference to match events by timestamp/order and calculate
event recall, precision, exact plate accuracy, character error rate, duplicate rate and missed
events. It is never passed to a detector or OCR model during inference.

## Current video smoke result

Pipeline `motorcycle-alpr-v2` completed the supplied 612.48-second, 1280x720, 25 FPS video:

- 69 review events after temporal consolidation;
- 31 events with a plausible model-read plate;
- 1 conservatively confirmed no-plate event;
- 37 unreadable/review events, including 16 suspected no-plate candidates;
- UTF-8 BOM CSV export: 69 rows, 20 columns, with evidence URLs and pipeline version.

These figures prove the executable end-to-end flow, not model accuracy. None of the 31 OCR values
may be called correct until the official GT Excel is matched by timestamp/order.

The UI-to-backend field audit is maintained in
[`17-ui-field-audit.md`](17-ui-field-audit.md).
