# UI field audit

This audit records which mockup elements are backed by the current database and HTTP API. The UI
must not imply that a disabled item is available.

## Create job

| UI element | Current source | Decision |
|---|---|---|
| Video upload | `POST /api/v1/jobs` raw body | Enabled |
| Image set | Database enum exists; create API rejects it | Disabled |
| Object mode `PLATE` | `processing_jobs.object_mode`; API fixes it to `PLATE` | Enabled, read-only |
| Face or combined mode | Database enum exists; API does not accept a selection | Disabled |
| Camera | `config_snapshot.camera=rear_toll_lane`; no camera-list API | Fixed server label |
| ROI selector | No public DTO or selector API | Read-only “no API” state |
| Processing mode | `processing_jobs.processing_mode`; API fixes it to `BALANCED` | Balanced enabled, others disabled |
| Sample rate | `processing_jobs.sample_rate`; API fixes it to `4.0` | Read-only 4 FPS |
| Save draft | Create endpoint returns a `DRAFT` job | Enabled |
| Start | `POST /api/v1/jobs/{id}/start` | Enabled after upload/draft |

## Processing

The page uses only `JobResponse`: source name, job code, status, current stage, progress, processed
frames, total frames, duration, dimensions, FPS, processing mode, sample rate and error message.
Source preview uses `GET /api/v1/jobs/{id}/source`. Retry uses the existing retry endpoint.

## Review

Prediction and evidence are backed by `GET /api/v1/jobs/{id}/results`:

- TrackID and timestamps;
- normalized/raw OCR and confidence;
- vehicle/plate bounding boxes and detection confidence;
- full frame, vehicle crop and plate crop URLs;
- detection counts, quality score and quality flags.

The database contains Ground Truth, duplicate and review-action tables, but the application has no
HTTP endpoint to read or mutate those records. Ground Truth fields, duplicate/discard actions, GT
Draft and GT Final therefore remain disabled and explicitly marked as requiring backend support.

## Overview and export

Overview KPIs and recent jobs are calculated from `GET /api/v1/jobs`. No chart or fabricated
analytics is shown. Export exposes only the implemented model-result CSV endpoint. There is no
fabricated export history.
