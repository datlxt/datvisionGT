# Evidence and benchmark workflow

This workflow implements the evidence prerequisite for plate detection and creates a
model-neutral smoke benchmark. It intentionally does not download or run a detector/OCR model.

## 1. Put a source video in persistent storage

On Windows PowerShell, from the repository root:

```powershell
New-Item -ItemType Directory -Force storage\uploads | Out-Null
Copy-Item ".\Lane9_highlights - Copy.mp4" ".\storage\uploads\lane9-sample.mp4"
```

Real media is ignored by Git. The original source is never modified by the extractor.

## 2. Build and extract a short smoke sample

```powershell
docker compose build backend
docker compose run --rm --no-deps backend python -m scripts.run_evidence `
  /app/storage/uploads/lane9-sample.mp4 `
  --storage-root /app/storage `
  --job-id lane9-smoke `
  --sample-rate 4 `
  --max-frames 20
```

Output is job-isolated:

```text
storage/jobs/lane9-smoke/
  evidence-manifest.json
  frames/
```

The manifest contains source SHA-256, codec, dimensions, time base, source PTS, presentation
timestamp, sampled target timestamp, frame hash, and VFR analysis.

Validate the output before using it as benchmark input:

```powershell
docker compose run --rm --no-deps backend python -m scripts.validate_evidence `
  /app/storage/jobs/lane9-smoke/evidence-manifest.json `
  --storage-root /app/storage
```

Remove `--max-frames` only after this smoke run passes. A job ID is permanently bound to its
source hash, sample rate, pipeline version, and JPEG quality. A retry with the same binding is
idempotent; use a new job ID when any of those inputs changes.

After the 20-frame smoke test passes, survey the entire sample video at one frame every five
seconds. This keeps the first annotation pass small while covering every highlighted vehicle:

```powershell
docker compose run --rm --no-deps backend python -m scripts.run_evidence `
  /app/storage/uploads/lane9-sample.mp4 `
  --storage-root /app/storage `
  --job-id lane9-survey `
  --sample-rate 0.2
```

## 3. Create benchmark v1

The command copies selected evidence frames into a portable dataset and creates empty annotation
records. `--every` controls temporal thinning after evidence sampling.

```powershell
docker compose run --rm --no-deps backend python -m scripts.create_benchmark_dataset `
  /app/storage/jobs/lane9-survey/evidence-manifest.json `
  --output-root /app/storage/datasets `
  --dataset-id lane9-survey-v1 `
  --every 1 `
  --max-images 300
```

Output:

```text
storage/datasets/lane9-survey-v1/
  dataset-manifest.json
  images/
  annotations/plates.jsonl
  splits/development.txt
  splits/validation.txt
  splits/test.txt
```

Dataset IDs are immutable. The command refuses to overwrite an existing dataset so that manual
annotations cannot be erased accidentally; choose a new versioned ID for a new selection.

The one-video split is contiguous in time (60/20/20). It is suitable for smoke testing only.
Release evaluation must split by independent video/camera/date so adjacent frames from one vehicle
cannot leak between validation and test.

## 4. Annotation contract

Edit the `plates` list for each JSONL image record. Coordinates use COCO-style
`[x, y, width, height]` and must remain inside the image.

```json
{
  "image_id": "lane9-smoke_000001",
  "file_name": "images/lane9-smoke_000001.jpg",
  "width": 1920,
  "height": 1080,
  "split": "development",
  "source": {
    "job_id": "lane9-smoke",
    "frame_index": 25,
    "pts": 225000,
    "timestamp_us": 250000,
    "sha256": "..."
  },
  "plates": [
    {
      "bbox": [910, 610, 82, 61],
      "plate_text": "29A112345",
      "layout": "two_line",
      "difficulty": "medium",
      "unreadable": false,
      "ignore": false
    }
  ]
}
```

Do not guess hidden characters. Use `unreadable: true` when the plate cannot be transcribed and
`ignore: true` only when the object is outside the evaluation policy.

Validate annotations:

```powershell
docker compose run --rm --no-deps backend python -m scripts.evaluate_benchmark `
  /app/storage/datasets/lane9-plate-v1/annotations/plates.jsonl
```

## 5. Model-neutral prediction contract

Every detector/OCR adapter writes the same JSONL structure:

```json
{
  "image_id": "lane9-smoke_000001",
  "plates": [
    {
      "bbox": [910, 610, 82, 61],
      "confidence": 0.93,
      "plate_text": "29A112345"
    }
  ]
}
```

Evaluate it:

```powershell
docker compose run --rm --no-deps backend python -m scripts.evaluate_benchmark `
  /app/storage/datasets/lane9-plate-v1/annotations/plates.jsonl `
  --predictions /app/storage/benchmarks/predictions.jsonl `
  --output /app/storage/benchmarks/lane9-v1-metrics.json
```

The report includes detection precision/recall, exact OCR accuracy on matched detections,
end-to-end exact accuracy, character error rate, and layout-level counts.

## Exit criteria

Evidence is ready when extraction is deterministic, timestamps are monotonic, every artifact hash
validates, paths cannot cross jobs, and retry does not create duplicate paths. Benchmark preparation
is ready when annotations validate, source evidence is traceable, temporal splits are explicit, and
two prediction runs can be compared using the same evaluator.
