# Database Schema — Plate MVP

The canonical schema is defined twice for two different responsibilities:

- SQLAlchemy mappings: `backend/app/models/`
- Versioned PostgreSQL DDL: `backend/migrations/versions/`

Alembic migrations are the deployment source of truth. PostgreSQL data is stored in the Docker
volume `datvision-gt_postgres_data`; it is not committed as a `.sql` file.

## Data flow

```text
users ───────────────┐
camera_configs ──────┼── processing_jobs ── job_events
model_versions ──────┼── job_models
                     │
                     └── artifacts
                           │
processing_jobs ── tracks ─┼── detections ── recognition_results
                     │     │
                     └─────┴── ground_truth_records ── review_actions
                                      │
                                      └── exports ── artifacts
```

## Tables

| Table | Responsibility |
|---|---|
| `users` | Admin, annotator, and reviewer identities. |
| `camera_configs` | Versioned ROI and camera-specific thresholds. |
| `model_versions` | Model runtime, version, path, SHA-256, and immutable config. |
| `processing_jobs` | Input metadata, progress, pipeline/config lineage, and status. |
| `job_models` | Exact detector/OCR/tracker versions used by a job. |
| `job_events` | Structured processing and failure events. |
| `artifacts` | Source video, full frames, crops, thumbnails, and export files. |
| `tracks` | One appearance/event across multiple frames. |
| `detections` | Frame/PTS/timestamp, bbox, confidence, and evidence artifacts. |
| `recognition_results` | Per-frame OCR and track-level multi-frame voting. |
| `ground_truth_records` | Human-reviewed plate value and final evidence state. |
| `review_actions` | Append-only review history snapshots. |
| `exports` | Draft/final export lifecycle and output artifact. |

## Integrity decisions

- UUIDs are application-generated; no PostgreSQL extension is required.
- Status-like values use named `CHECK` constraints. They remain easy to migrate without PostgreSQL
  enum replacement operations.
- Config and raw model payloads use JSONB, while queryable business fields remain normal columns.
- Composite foreign keys include `job_id` for track, detection, Ground Truth, and artifact links.
  PostgreSQL therefore rejects evidence accidentally linked across jobs.
- A partial unique index allows only one `is_best = true` detection per track.
- A verified GT record must have normalized text, valid evidence, verifier, and verification time.
- Review actions retain before/after JSON snapshots instead of overwriting history.
- Video and image bytes remain in storage; PostgreSQL stores artifact metadata and hashes only.

## Verification

```powershell
.\scripts\dev.ps1 migrate
.\scripts\dev.ps1 schema-check
```

The schema check confirms required tables, rejects an invalid job status, and verifies that a
detection cannot reference a crop belonging to a different job.

