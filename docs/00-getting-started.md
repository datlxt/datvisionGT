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

The sample video may stay outside Git. In a later phase the upload API will copy it into a
job-specific storage directory and record its SHA-256 hash.
