# Getting Started

DatVision GT currently implements the deployable Phase 0 skeleton for a plate-first pipeline.
No detector or OCR model is bundled yet.

Runtime Python dependencies are pinned in `backend/requirements.lock.txt`. Update the human-edited
`backend/requirements.txt` first, validate the image, then regenerate the lock when dependencies
are intentionally upgraded.

## Local development on Windows

Prerequisites:

- Docker Desktop using the WSL 2 backend.
- Docker Compose v2 or later.

Start Docker Desktop, then run:

```powershell
Copy-Item .env.example .env
.\scripts\dev.ps1 config
.\scripts\dev.ps1 up
.\scripts\dev.ps1 migrate
.\scripts\dev.ps1 smoke
```

Open:

- Application: http://localhost:5173
- Swagger through the gateway: http://localhost:5173/docs
- Readiness: http://localhost:5173/api/v1/health

Stop the stack:

```powershell
.\scripts\dev.ps1 down
```

## Data layout

Real videos, frames, crops, models, and exports are ignored by Git.

```text
storage/
  uploads/
  jobs/
  exports/
models/
```

The upload API writes each video into a job-specific directory, probes it with PyAV, and records
its SHA-256 hash before the job can be queued.

## Evidence and benchmark smoke test

The timestamp-aware evidence extractor and model-neutral benchmark workflow are documented in
[`15-evidence-and-benchmark.md`](15-evidence-and-benchmark.md). Run the limited 20-frame smoke test
there before extracting a full video or integrating a detector.

The executable motorcycle ALPR design, model provisioning, `NO_PLATE` rule, APIs, and deployment
profile are documented in [`16-motorcycle-alpr-mvp.md`](16-motorcycle-alpr-mvp.md).
