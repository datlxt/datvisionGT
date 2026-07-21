# DatVision GT

<p align="center">
  <strong>Ground Truth Generation & Verification Platform</strong>
</p>

<p align="center">
  Mọi kết quả đều phải có bằng chứng.
</p>

---

## 1. Giới thiệu nhanh

**DatVision GT** là nền tảng web hỗ trợ tạo và kiểm duyệt Ground Truth từ video hoặc tập ảnh tại hầm gửi xe.

Hệ thống hỗ trợ hai nhóm đối tượng:

1. **Biển số — phạm vi ưu tiên của MVP hiện tại**
   - Phát hiện biển số.
   - OCR biển số.
   - Gom kết quả từ nhiều frame.
   - Gán TrackID.
   - Loại kết quả trùng.
   - Kiểm duyệt Ground Truth.

2. **Khuôn mặt — giai đoạn sau**
   - Phát hiện khuôn mặt.
   - Theo dõi khuôn mặt qua nhiều frame.
   - Gán TrackID.
   - Chọn frame rõ nhất.
   - Nhận diện danh tính theo gallery.
   - Phân loại Known hoặc Unknown.
   - Kiểm duyệt và cập nhật Ground Truth.

Luồng chính:

```text
Video/Tập ảnh
    ↓
Phát hiện khuôn mặt/biển số
    ↓
Theo dõi đối tượng qua nhiều frame
    ↓
Gán TrackID
    ↓
Chọn frame rõ nhất
    ↓
Nhận diện tên người/biển số
    ↓
Phát hiện và xử lý duplicate
    ↓
Sinh GT Draft
    ↓
Con người kiểm duyệt
    ↓
Xuất GT Final
```

> Model output không phải Ground Truth cuối cùng.

Hệ thống tạo:

- **GT Draft:** dữ liệu sơ bộ do tool sinh ra.
- **GT Final:** dữ liệu đã được con người kiểm tra và xác nhận.

---

# 2. Quick Start

Phần này giúp dev clone repository, setup môi trường và chạy được toàn bộ codebase.

> **Trạng thái triển khai hiện tại:** repository đã có Phase 0 theo hướng **plate-first**.
> Backend, RQ worker, PostgreSQL, Redis và React/Caddy đã chạy được bằng Docker Compose;
> detector/OCR, upload, authentication và review workflow chưa được tích hợp. Hướng dẫn đang chạy
> thực tế nằm tại `docs/00-getting-started.md`. Các mục chưa được implement bên dưới được giữ lại
> như target specification, không phải command đã sẵn sàng.

## 2.1. Yêu cầu hệ thống

Cần cài đặt:

- Git.
- Docker Desktop hoặc Docker Engine.
- Docker Compose v2.
- Make — khuyến nghị nhưng không bắt buộc.
- NVIDIA Driver và NVIDIA Container Toolkit — chỉ cần nếu chạy model bằng GPU.

Không bắt buộc phải cài trực tiếp:

- PostgreSQL.
- Redis.
- Python.
- Node.js.

Các thành phần trên có thể chạy hoàn toàn bằng Docker.

### Phiên bản khuyến nghị

| Thành phần | Phiên bản |
|---|---:|
| Python | 3.12 |
| Node.js | 22 LTS |
| PostgreSQL | 16 |
| Redis | 7 |
| Docker Compose | v2 |
| FastAPI | Theo `pyproject.toml` |
| React | Theo `package.json` |

---

## 2.2. Clone repository

```bash
git clone <REPOSITORY_URL>
cd datvision-gt
```

Thay `<REPOSITORY_URL>` bằng URL repository thật.

---

## 2.3. Tạo file môi trường

Sao chép file mẫu:

```bash
cp .env.example .env
```

Trên Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Mở file `.env` và cập nhật cấu hình:

```env
# ==================================================
# APPLICATION
# ==================================================

APP_NAME=DatVision GT
APP_ENV=development
APP_DEBUG=true

BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000

FRONTEND_HOST=0.0.0.0
FRONTEND_PORT=5173

# ==================================================
# POSTGRESQL
# ==================================================

POSTGRES_DB=datvision_gt
POSTGRES_USER=datvision
POSTGRES_PASSWORD=change_me
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

DATABASE_URL=postgresql+psycopg://datvision:change_me@postgres:5432/datvision_gt

# ==================================================
# REDIS
# ==================================================

REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0
REDIS_URL=redis://redis:6379/0

# ==================================================
# AUTHENTICATION
# ==================================================

JWT_SECRET_KEY=change_me_to_a_long_random_secret
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60

# ==================================================
# STORAGE
# ==================================================

STORAGE_ROOT=/app/storage
UPLOAD_ROOT=/app/storage/uploads
JOB_STORAGE_ROOT=/app/storage/jobs
EXPORT_ROOT=/app/storage/exports

# ==================================================
# MODEL
# ==================================================

MODEL_ROOT=/app/models

FACE_DETECTOR_MODEL_PATH=/app/models/face_detection
FACE_RECOGNIZER_MODEL_PATH=/app/models/face_recognition
PLATE_DETECTOR_MODEL_PATH=/app/models/plate_detection
PLATE_OCR_MODEL_PATH=/app/models/plate_ocr

# ==================================================
# PROCESSING
# ==================================================

DEFAULT_SAMPLE_RATE=4
DEFAULT_PROCESSING_MODE=HIGH_RECALL

FACE_DETECTION_THRESHOLD=0.50
FACE_RECOGNITION_THRESHOLD=0.78
PLATE_DETECTION_THRESHOLD=0.50
PLATE_OCR_THRESHOLD=0.60

MINIMUM_TRACK_LENGTH=2
MAX_TRACK_RECONNECT_GAP_MS=1500

# ==================================================
# CORS
# ==================================================

CORS_ORIGINS=http://localhost:5173

# ==================================================
# LOGGING
# ==================================================

LOG_LEVEL=INFO
```

Không commit file `.env` lên Git.

---

## 2.4. Tạo các thư mục cần thiết

```bash
mkdir -p storage/uploads
mkdir -p storage/jobs
mkdir -p storage/exports
mkdir -p models
```

Trên Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force storage/uploads
New-Item -ItemType Directory -Force storage/jobs
New-Item -ItemType Directory -Force storage/exports
New-Item -ItemType Directory -Force models
```

---

## 2.5. Build Docker image

```bash
docker compose build
```

Nếu repository có `Makefile`:

```bash
make build
```

---

## 2.6. Khởi động PostgreSQL và Redis

```bash
docker compose up -d postgres redis
```

Kiểm tra trạng thái:

```bash
docker compose ps
```

Kết quả mong đợi:

```text
postgres   running
redis      running
```

Xem log PostgreSQL:

```bash
docker compose logs -f postgres
```

Xem log Redis:

```bash
docker compose logs -f redis
```

---

## 2.7. Chạy database migration

```bash
docker compose run --rm backend alembic upgrade head
```

Hoặc:

```bash
make migrate
```

Kiểm tra migration hiện tại:

```bash
docker compose run --rm backend alembic current
```

Xem lịch sử migration:

```bash
docker compose run --rm backend alembic history
```

---

## 2.8. Tạo tài khoản Admin đầu tiên

```bash
docker compose run --rm backend \
  python scripts/create_admin.py \
  --email admin@datvision.local \
  --password demo123 \
  --name "DatVision Admin"
```

Tài khoản development:

```text
Email: admin@datvision.local
Password: demo123
```

Không sử dụng mật khẩu demo trong production.

---

## 2.9. Khởi động toàn bộ hệ thống

```bash
docker compose up -d
```

Hoặc:

```bash
make up
```

Các service dự kiến:

| Service | Địa chỉ |
|---|---|
| Frontend | http://localhost:5173 |
| Backend API | http://localhost:8000 |
| Swagger API | http://localhost:8000/docs |
| OpenAPI JSON | http://localhost:8000/openapi.json |
| PostgreSQL | localhost:5432 |
| Redis | localhost:6379 |

---

## 2.10. Kiểm tra Health Check

```bash
curl http://localhost:8000/api/v1/health
```

Kết quả mong đợi:

```json
{
  "status": "ok",
  "database": "connected",
  "redis": "connected",
  "storage": "available"
}
```

Kiểm tra frontend:

```text
http://localhost:5173
```

---

## 2.11. Kiểm tra log

Backend:

```bash
docker compose logs -f backend
```

Worker:

```bash
docker compose logs -f worker
```

Frontend:

```bash
docker compose logs -f frontend
```

Toàn bộ hệ thống:

```bash
docker compose logs -f
```

---

## 2.12. Dừng hệ thống

```bash
docker compose down
```

Hoặc:

```bash
make down
```

Dừng và xóa volume database:

```bash
docker compose down -v
```

> Cảnh báo: `docker compose down -v` sẽ xóa dữ liệu PostgreSQL trong môi trường local.

---

# 3. Kiểm tra nhanh luồng MVP

Sau khi hệ thống chạy:

1. Truy cập frontend.
2. Đăng nhập bằng tài khoản development.
3. Chọn **Tạo job**.
4. Upload video hoặc tập ảnh.
5. Chọn phạm vi xử lý:
   - Khuôn mặt.
   - Biển số.
   - Khuôn mặt và biển số.
6. Chọn camera preset.
7. Chọn:
   - Toàn bộ frame.
   - ROI đã cấu hình.
8. Chọn processing mode:
   - High Recall.
   - Balanced.
   - Fast.
9. Bắt đầu xử lý.
10. Theo dõi tiến độ.
11. Mở màn Kiểm duyệt GT.
12. Xem:
   - Full frame.
   - Crop.
   - Timestamp.
   - Frame number.
   - TrackID.
   - Prediction.
   - Confidence.
13. Nhập hoặc sửa Ground Truth.
14. Xác nhận, discard hoặc đánh dấu duplicate.
15. Bổ sung missed case nếu model bỏ sót.
16. Export GT Draft hoặc GT Final.

---

# 4. Nguyên tắc cốt lõi

## 4.1. No evidence, no record

Mọi record trong hệ thống bắt buộc phải truy xuất được về:

- File video hoặc ảnh nguồn.
- Job xử lý.
- Frame number thật.
- Timestamp thật.
- Bounding box thật.
- Full frame thật.
- Crop được tạo từ chính full frame.
- TrackID.
- Model version.
- Configuration version.

Không được export record nếu không có evidence hợp lệ.

Mỗi record phải trả lời được:

```text
Record này đến từ video nào?
Frame bao nhiêu?
Timestamp nào?
Bounding box ở đâu?
Crop được lấy từ frame nào?
Model nào sinh ra kết quả?
Config nào được sử dụng?
```

---

## 4.2. Confidence không phải Ground Truth

Confidence chỉ thể hiện mức độ tự tin của model.

Confidence cao không đảm bảo prediction đúng.

Ví dụ:

```text
Prediction: 29N196452
Confidence: 97%

Ground Truth thực tế: 29N1964S2
```

Hoặc:

```text
Predicted Person: PERSON_001
Confidence: 95%

Người thực tế: PERSON_003
```

Do đó:

```text
Model Prediction ≠ Ground Truth
```

Chỉ record được con người xác nhận mới trở thành GT Final.

---

## 4.3. Không ép nhận diện khuôn mặt

Nếu similarity thấp hơn threshold:

```text
Similarity < Recognition Threshold
→ UNKNOWN
```

Không được tự động chọn người gần nhất trong gallery khi similarity không đủ.

Ví dụ:

```text
Similarity: 0.86
Threshold: 0.78
→ PERSON_001
```

```text
Similarity: 0.62
Threshold: 0.78
→ UNKNOWN
```

---

## 4.4. Một lượt xuất hiện tương ứng một event

Một người xuất hiện trong 50 frame không được tạo 50 dòng GT.

Kết quả mong muốn:

```text
TrackID: FACE_0001
Start: 00:01:20
End: 00:01:28
Best Frame: 2135
Detection Count: 50
```

Không tạo:

```text
FACE_0001_FRAME_01
FACE_0001_FRAME_02
FACE_0001_FRAME_03
...
```

---

## 4.5. Job isolation

Mỗi processing job phải có:

- JobID riêng.
- Thư mục lưu trữ riêng.
- Log riêng.
- Output riêng.
- Track và detection riêng.

Ví dụ:

```text
storage/jobs/JOB_20260721_0001/
storage/jobs/JOB_20260721_0002/
```

Không được để hai job dùng chung:

```text
storage/output/result.xlsx
```

---

# 5. Bối cảnh và bài toán

Trong quá trình kiểm thử các model nhận diện khuôn mặt và biển số tại hầm gửi xe, đội QA cần tạo Ground Truth để:

- So sánh output model với dữ liệu thực tế.
- Đánh giá đúng hoặc sai.
- Phân loại lỗi.
- Tính Precision.
- Tính Recall.
- Xác định False Positive.
- Xác định False Negative.
- Xác định Duplicate.
- Theo dõi thời gian đối tượng xuất hiện.
- Lưu frame và crop làm evidence.
- Dùng lại bộ Ground Truth để đánh giá model khác.

Quy trình thủ công hiện tại:

1. Mở video.
2. Xem từng đoạn.
3. Tìm thời điểm có người hoặc phương tiện.
4. Chụp frame.
5. Crop khuôn mặt hoặc biển số.
6. Nhập timestamp.
7. Nhập tên người hoặc biển số.
8. Phân loại trường hợp.
9. Kiểm tra duplicate.
10. Tạo file Excel.

Các vấn đề:

- Mất nhiều thời gian.
- Dễ sai timestamp.
- Dễ chọn sai frame.
- Dễ bỏ sót đối tượng.
- Dễ ghi trùng một đối tượng.
- Khó đồng nhất giữa các annotator.
- Khó truy vết kết quả.
- Khó đánh giá lại model sau này.

DatVision GT hỗ trợ tự động hóa phần lớn quá trình trên nhưng vẫn giữ con người trong vòng kiểm duyệt.

---

# 6. Mục tiêu sản phẩm

## 6.1. Mục tiêu chính

- Giảm thời gian xem video thủ công.
- Tự động tìm frame có khuôn mặt hoặc biển số.
- Tự động crop đối tượng.
- Tự động tracking qua nhiều frame.
- Tự động gán TrackID.
- Tự động chọn best frame.
- Tự động sinh GT Draft.
- Hỗ trợ người dùng sửa và xác nhận GT.
- Giảm duplicate.
- Giảm false record.
- Bổ sung được missed case.
- Xuất Excel có đầy đủ evidence.
- Có thể dùng bộ GT để đánh giá model khác.

---

## 6.2. Ưu tiên sản phẩm

### Ưu tiên 1 — Biển số

- Chọn hoặc tích hợp model LPR phù hợp; hiện chưa có model.
- Plate Detection.
- OCR.
- Multi-frame Voting.
- Plate Tracking.
- Duplicate Handling.
- Ground Truth Verification.

Không ưu tiên train mới toàn bộ LPR model trong MVP.

### Ưu tiên 2 — Khuôn mặt

- Face Detection.
- Face Tracking.
- Best-frame Selection.
- Face Alignment.
- Face Embedding.
- Gallery Matching.
- Known/Unknown Classification.
- Ground Truth Verification.

---

# 7. MVP Scope

MVP tập trung chứng minh luồng giá trị cốt lõi:

```text
Tạo job
→ Xử lý video
→ Tạo TrackID
→ Chọn best frame
→ Sinh GT Draft
→ Con người kiểm duyệt
→ Xử lý Duplicate/Discard
→ Bổ sung Missed Case
→ Export Excel
```

## 7.1. Chức năng trong MVP

### Tổng quan

- Hiển thị tổng job.
- Job đang xử lý.
- Job chờ kiểm duyệt.
- Job đã hoàn thành.
- Danh sách job gần đây.

### Tạo job

- Upload video.
- Upload tập ảnh.
- Chọn Face, Plate hoặc Both.
- Chọn camera preset.
- Chọn toàn frame hoặc ROI có sẵn.
- Chọn processing mode.
- Chọn sample rate.
- Bắt đầu xử lý.

### Processing

- Background processing.
- Progress percentage.
- Frames processed.
- Detection count.
- Track count.
- Elapsed time.
- Remaining time.
- Cancel job.

### Kiểm duyệt GT

- Danh sách record.
- Full frame.
- Crop.
- Timestamp.
- Frame number.
- TrackID.
- Prediction.
- Confidence.
- Classification.
- GT Text.
- Verify.
- Discard.
- Duplicate.
- Note.
- Manual missed case.

### Export

- GT Draft.
- GT Final.
- Excel.
- CSV.
- Crop ZIP.
- Summary sheet.
- Config sheet.

---

## 7.2. Không thuộc MVP

Phát triển sau:

- Gallery Management UI đầy đủ.
- Camera Management UI đầy đủ.
- Audit History UI.
- User Management nâng cao.
- Role workflow nhiều cấp.
- Merge Track nâng cao.
- Split Track nâng cao.
- Vẽ polygon ROI trên frontend.
- Vẽ bounding box trực tiếp trên video.
- Model Management.
- So sánh nhiều model.
- RTSP realtime.
- Streaming nhiều camera.
- Dashboard analytics nâng cao.
- MinIO hoặc S3 production.
- Review workflow hai cấp.
- Active Learning.
- Model retraining pipeline.

---

# 8. Các màn hình MVP

Sidebar MVP gồm đúng bốn mục:

```text
DatVision GT

▦ Tổng quan
＋ Tạo job
✓ Kiểm duyệt GT
⇩ Kết quả & Export
```

## 8.1. Tổng quan

Hiển thị bốn KPI:

- Tổng job.
- Đang xử lý.
- Chờ kiểm duyệt.
- Đã hoàn thành.

Bảng Job gần đây:

| Tên dữ liệu | Loại | Tiến độ | Track | Kiểm duyệt | Trạng thái |
|---|---|---:|---:|---:|---|

Không ưu tiên biểu đồ phức tạp trong MVP.

---

## 8.2. Tạo job

Form gồm ba khu vực:

### Dữ liệu đầu vào

- Video.
- Tập ảnh.
- Drag and drop.
- File name.
- File size.
- Duration.
- Resolution.
- FPS.

### Phạm vi xử lý

- Face.
- Plate.
- Both.
- Camera preset.
- Whole frame.
- Camera ROI.

### Cấu hình xử lý

- High Recall.
- Balanced.
- Fast.
- Sample rate.
- Detection threshold.
- Recognition threshold.
- Minimum track length.
- Enable duplicate detection.

---

## 8.3. Processing

Hiển thị:

- Video preview.
- Current frame.
- Bounding box.
- Progress.
- Frames scanned.
- Detection count.
- Track count.
- Elapsed time.
- Estimated remaining.
- Cancel.
- Run in background.

---

## 8.4. Kiểm duyệt GT

Bố cục desktop:

```text
┌──────────────────────────────────────────────────────────┐
│ Search | Filter | Job Progress                            │
├──────────────────┬────────────────────────┬───────────────┤
│ Record List      │ Evidence Viewer        │ GT Form       │
│                  │                        │               │
│ Crop             │ Full Frame             │ Prediction    │
│ TrackID          │ Bounding Box           │ GT Text       │
│ Timestamp        │ Nearby Frames          │ Label         │
│ Confidence       │ Best Crop              │ Verify        │
└──────────────────┴────────────────────────┴───────────────┘
```

Màn này là core của sản phẩm.

---

## 8.5. Kết quả & Export

Hiển thị:

- Job.
- Tổng record.
- Verified.
- Unverified.
- Duplicate.
- Discard.
- Manual additions.
- Export status.

Các action:

- Export GT Draft.
- Export GT Final.
- Download Excel.
- Download CSV.
- Download Crop ZIP.

---

# 9. Kiến trúc hệ thống

```text
┌────────────────────────────────────┐
│ Frontend                           │
│ React + TypeScript + Vite          │
│ Tailwind CSS + shadcn/ui           │
└─────────────────┬──────────────────┘
                  │ REST API / Polling
┌─────────────────▼──────────────────┐
│ Backend                            │
│ FastAPI + Pydantic                 │
│ Authentication / Job / GT / Export│
└─────────┬─────────────────┬────────┘
          │                 │
┌─────────▼────────┐ ┌──────▼────────┐
│ PostgreSQL       │ │ Redis         │
│ Metadata / GT    │ │ Queue / State │
└──────────────────┘ └──────┬────────┘
                            │
                   ┌────────▼─────────┐
                   │ Background Worker│
                   │ RQ               │
                   └────────┬─────────┘
                            │
                   ┌────────▼─────────┐
                   │ Vision Pipeline  │
                   │ Detect           │
                   │ Track            │
                   │ Recognize        │
                   │ Deduplicate      │
                   └────────┬─────────┘
                            │
                   ┌────────▼─────────┐
                   │ File Storage     │
                   │ Video/Frame/Crop │
                   │ Excel/Log        │
                   └──────────────────┘
```

---

# 10. Technology Stack

## 10.1. Frontend

- React.
- TypeScript.
- Vite.
- Tailwind CSS.
- shadcn/ui.
- TanStack Query.
- TanStack Table.
- React Hook Form.
- Zod.
- Axios.
- React Router.

## 10.2. Backend

- Python.
- FastAPI.
- Pydantic.
- SQLAlchemy.
- Alembic.
- Uvicorn.

## 10.3. Database

- PostgreSQL.
- pgvector nếu cần lưu face embedding trong database.

## 10.4. Background Processing

- Redis.
- RQ trong MVP.
- Có thể chuyển sang Celery khi mở rộng.

## 10.5. Computer Vision

### Video

- OpenCV.
- FFmpeg.
- NumPy.
- Pillow.

### Face

- InsightFace.
- SCRFD hoặc RetinaFace.
- ArcFace.
- ByteTrack hoặc DeepSORT.
- Cosine Similarity.
- FAISS hoặc pgvector khi gallery lớn.

### Plate

- YOLO Plate Detector.
- LPR model nội bộ.
- PaddleOCR hoặc OCR hiện có.
- Multi-frame Voting.

## 10.6. Export

- OpenPyXL.
- Pandas.
- XlsxWriter nếu cần.

## 10.7. Testing và Quality

- Pytest.
- Ruff.
- Mypy.
- ESLint.
- Prettier.
- Pre-commit.
- GitHub Actions.
- Docker Compose.

---

# 11. Cấu trúc repository

```text
datvision-gt/
├── README.md
├── CLAUDE.md
├── AGENTS.md
├── CONTRIBUTING.md
├── CHANGELOG.md
├── LICENSE
├── .env.example
├── .gitignore
├── docker-compose.yml
├── Makefile
│
├── docs/
│   ├── 00-getting-started.md
│   ├── 01-product-overview.md
│   ├── 02-mvp-scope.md
│   ├── 03-functional-specification.md
│   ├── 04-system-architecture.md
│   ├── 05-database-schema.md
│   ├── 06-face-pipeline.md
│   ├── 07-plate-pipeline.md
│   ├── 08-tracking-deduplication.md
│   ├── 09-api-specification.md
│   ├── 10-frontend-specification.md
│   ├── 11-excel-output.md
│   ├── 12-test-plan.md
│   ├── 13-security-and-data.md
│   ├── 14-deployment-guide.md
│   ├── 15-user-guide.md
│   ├── 16-sprint-plan.md
│   └── 17-known-limitations.md
│
├── configs/
│   ├── app.yaml
│   ├── models.yaml
│   ├── labels.yaml
│   ├── thresholds.yaml
│   └── cameras/
│       ├── lane9.yaml
│       ├── lane10.yaml
│       └── basement_b2.yaml
│
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   ├── auth/
│   │   ├── core/
│   │   ├── db/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   ├── repositories/
│   │   └── workers/
│   ├── migrations/
│   └── tests/
│
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   ├── pages/
│   │   ├── components/
│   │   ├── features/
│   │   ├── hooks/
│   │   ├── services/
│   │   ├── types/
│   │   └── utils/
│   └── tests/
│
├── vision/
│   ├── common/
│   ├── video/
│   ├── detection/
│   ├── tracking/
│   ├── recognition/
│   ├── quality/
│   ├── dedup/
│   ├── evidence/
│   └── pipeline.py
│
├── export/
│   ├── excel_exporter.py
│   ├── csv_exporter.py
│   └── summary_generator.py
│
├── scripts/
│   ├── create_admin.py
│   ├── seed_data.py
│   ├── run_pipeline.py
│   ├── build_gallery.py
│   ├── validate_evidence.py
│   └── export_job.py
│
├── models/
│   ├── face_detection/
│   ├── face_recognition/
│   ├── plate_detection/
│   └── plate_ocr/
│
├── storage/
│   ├── uploads/
│   ├── jobs/
│   └── exports/
│
└── tests/
    ├── unit/
    ├── integration/
    ├── e2e/
    ├── fixtures/
    └── sample_data/
```

---

# 12. Cấu trúc dữ liệu lưu trữ

```text
storage/
├── uploads/
│   ├── videos/
│   └── images/
│
├── jobs/
│   └── JOB_20260721_0001/
│       ├── source/
│       ├── full_frames/
│       ├── crops/
│       │   ├── faces/
│       │   └── plates/
│       ├── thumbnails/
│       ├── draft/
│       ├── final/
│       ├── logs/
│       └── metadata.json
│
└── exports/
    ├── GT_DRAFT_JOB_20260721_0001.xlsx
    └── GT_FINAL_JOB_20260721_0001.xlsx
```

PostgreSQL không lưu trực tiếp video hoặc ảnh lớn.

PostgreSQL lưu:

- Metadata.
- File path.
- Job.
- Track.
- Detection.
- Recognition result.
- Ground Truth.
- Export history.

---

# 13. Database PostgreSQL

## 13.1. Bảng `users`

Lưu người dùng hệ thống.

Các trường chính:

```text
id
full_name
email
password_hash
role
is_active
created_at
updated_at
```

Role dự kiến:

```text
ADMIN
ANNOTATOR
REVIEWER
```

Trong MVP có thể chỉ sử dụng một role đơn giản.

---

## 13.2. Bảng `camera_configs`

```text
id
name
camera_code
description
roi_config
default_sample_rate
face_threshold
plate_threshold
tracking_config
is_active
created_at
updated_at
```

Các trường config động dùng PostgreSQL `JSONB`.

---

## 13.3. Bảng `model_versions`

```text
id
model_type
name
version
file_path
config
is_active
created_at
```

Model type:

```text
FACE_DETECTOR
FACE_RECOGNIZER
FACE_TRACKER
PLATE_DETECTOR
PLATE_OCR
```

---

## 13.4. Bảng `processing_jobs`

```text
id
job_code
source_type
source_name
source_path
source_hash
camera_config_id
status
processing_mode
object_mode
sample_rate
total_frames
processed_frames
duration_ms
progress
started_at
completed_at
error_message
created_by
created_at
updated_at
```

Object mode:

```text
FACE
PLATE
BOTH
```

Status:

```text
DRAFT
PENDING
QUEUED
PROCESSING
WAITING_FOR_REVIEW
COMPLETED
FAILED
CANCELLED
```

---

## 13.5. Bảng `tracks`

```text
id
job_id
track_code
object_type
start_frame
end_frame
best_frame
start_timestamp_ms
end_timestamp_ms
best_timestamp_ms
best_detection_id
quality_score
classification
is_duplicate
duplicate_of
track_status
event_key
created_at
updated_at
```

---

## 13.6. Bảng `detections`

```text
id
job_id
track_id
frame_number
timestamp_ms
bbox_x1
bbox_y1
bbox_x2
bbox_y2
detection_confidence
quality_score
full_frame_path
crop_path
thumbnail_path
landmarks
raw_output
created_at
```

---

## 13.7. Bảng `recognition_results`

```text
id
track_id
recognition_type
predicted_text
predicted_identity_id
recognition_confidence
ocr_confidence
similarity_score
model_version_id
raw_output
created_at
```

---

## 13.8. Bảng `ground_truth_records`

```text
id
track_id
gt_text
gt_identity_id
classification
verify_status
discard
is_duplicate
duplicate_of
note
verified_by
verified_at
created_at
updated_at
```

---

## 13.9. Bảng `exports`

```text
id
job_id
export_type
file_path
record_count
created_by
created_at
```

Export type:

```text
GT_DRAFT
GT_FINAL
SUMMARY
CROP_ZIP
```

Chi tiết database xem:

```text
docs/05-database-schema.md
```

---

# 14. API tổng quan

Base URL:

```text
/api/v1
```

## Authentication

```http
POST /api/v1/auth/login
POST /api/v1/auth/logout
GET  /api/v1/auth/me
```

## Upload

```http
POST /api/v1/uploads/video
POST /api/v1/uploads/images
```

## Jobs

```http
POST   /api/v1/jobs
GET    /api/v1/jobs
GET    /api/v1/jobs/{job_id}
POST   /api/v1/jobs/{job_id}/start
POST   /api/v1/jobs/{job_id}/cancel
POST   /api/v1/jobs/{job_id}/rescan
GET    /api/v1/jobs/{job_id}/progress
DELETE /api/v1/jobs/{job_id}
```

## Tracks

```http
GET   /api/v1/jobs/{job_id}/tracks
GET   /api/v1/tracks/{track_id}
PATCH /api/v1/tracks/{track_id}
```

## Ground Truth

```http
GET   /api/v1/jobs/{job_id}/ground-truth
PATCH /api/v1/ground-truth/{record_id}

POST /api/v1/ground-truth/{record_id}/verify
POST /api/v1/ground-truth/{record_id}/discard
POST /api/v1/ground-truth/{record_id}/restore

POST /api/v1/jobs/{job_id}/ground-truth/manual
```

## Export

```http
POST /api/v1/jobs/{job_id}/export/draft
POST /api/v1/jobs/{job_id}/export/final

GET /api/v1/jobs/{job_id}/exports
GET /api/v1/exports/{export_id}/download
```

Chi tiết request và response xem:

```text
docs/09-api-specification.md
```

---

# 15. Pipeline khuôn mặt

## 15.1. Đọc video

Metadata cần lấy:

- Width.
- Height.
- FPS.
- Total frames.
- Duration.
- Codec.
- Variable Frame Rate hoặc Constant Frame Rate.
- SHA-256 file hash.

Không chỉ dựa vào tên file.

---

## 15.2. Frame Sampling

Ví dụ:

```text
Video FPS: 25
Sample Rate: 5 FPS
→ Xử lý một frame sau mỗi năm frame gốc
```

Mode:

| Mode | Mô tả |
|---|---|
| High Recall | Ưu tiên không bỏ sót |
| Balanced | Cân bằng tốc độ và chất lượng |
| Fast | Test nhanh |

Ground Truth nên mặc định dùng High Recall.

---

## 15.3. Face Detection

Mỗi detection cần trả về:

```json
{
  "frame_number": 1250,
  "timestamp_ms": 50000,
  "bbox": {
    "x1": 310,
    "y1": 140,
    "x2": 430,
    "y2": 290
  },
  "confidence": 0.94,
  "landmarks": []
}
```

Detection hợp lệ khi:

- Confidence đạt threshold.
- Bbox nằm trong frame.
- Crop không rỗng.
- Khuôn mặt đủ kích thước.
- Detection nằm trong ROI.
- Frame nguồn tồn tại.

---

## 15.4. Tracking

Tracking sử dụng:

- IoU.
- Khoảng cách tâm bbox.
- Thời gian.
- Hướng di chuyển.
- Face embedding.
- Camera ID.

Output:

```json
{
  "track_id": "FACE_000001",
  "start_frame": 1200,
  "end_frame": 1420,
  "start_timestamp_ms": 48000,
  "end_timestamp_ms": 56800
}
```

---

## 15.5. Best-frame Selection

Mỗi crop được chấm điểm dựa trên:

- Sharpness.
- Face size.
- Frontal pose.
- Brightness.
- Detection confidence.
- Completeness.
- Occlusion.
- Blur.

Ví dụ:

```text
Quality Score =
    Sharpness Weight
  + Face Size Weight
  + Frontal Pose Weight
  + Brightness Weight
  + Detection Confidence Weight
  + Completeness Weight
  - Occlusion Penalty
  - Blur Penalty
```

Best frame phải là frame có chất lượng tốt nhất trong track, không phải frame đầu tiên.

---

## 15.6. Face Recognition

```text
Best Crop
→ Face Alignment
→ Generate Embedding
→ Compare with Gallery
→ Get Highest Similarity
→ Apply Threshold
→ Known hoặc Unknown
```

Output Known:

```json
{
  "predicted_person_id": "PERSON_001",
  "predicted_name": "Person 001",
  "similarity": 0.86,
  "recognition_status": "KNOWN"
}
```

Output Unknown:

```json
{
  "predicted_person_id": null,
  "predicted_name": "UNKNOWN",
  "similarity": 0.63,
  "recognition_status": "UNKNOWN"
}
```

---

# 16. Pipeline biển số

## 16.1. Plate Detection

Hai phương án:

### Phương án 1

```text
Full Frame
→ Detect Plate
```

### Phương án 2

```text
Detect Vehicle
→ Crop Vehicle
→ Detect Plate trong Vehicle Crop
```

Camera xa hoặc nhiều vật thể nên dùng phương án 2.

---

## 16.2. Multi-frame OCR

Một track có thể có kết quả:

```text
Frame 100: 29N196452 — 91%
Frame 104: 29N196452 — 97%
Frame 108: 29N1964S2 — 83%
```

Không lấy kết quả từ một frame duy nhất.

Điểm tổng hợp:

```text
Final Score =
OCR Confidence
× Detection Confidence
× Image Quality
```

---

## 16.3. Chuẩn hóa biển số

Ví dụ:

```text
29-N1 964.52
→ 29N196452
```

Các bước:

- Uppercase.
- Xóa khoảng trắng.
- Xóa dấu gạch ngang.
- Xóa dấu chấm.
- Chuẩn hóa Unicode.
- Kiểm tra pattern biển số.

Không thay cứng toàn bộ:

```text
O → 0
I → 1
B → 8
```

Chỉ tạo candidate và chọn candidate phù hợp pattern.

---

# 17. Duplicate Handling

## 17.1. Duplicate trong cùng TrackID

Một người hoặc biển số xuất hiện trong nhiều frame.

Giải pháp:

- Một TrackID chỉ export một record.
- Các detection còn lại lưu trong bảng detections.
- Chọn best frame.

---

## 17.2. Track Fragmentation

Ví dụ:

```text
FACE_001: 00:12–00:16
FACE_017: 00:17–00:21
```

Hai track có thể là một người nếu:

```text
time_gap <= threshold
AND spatial_continuity = true
AND direction_match = true
AND embedding_similarity >= threshold
```

Không merge chỉ dựa vào similarity.

---

## 17.3. Duplicate trong MVP

MVP chưa bắt buộc Merge/Split nâng cao.

Người kiểm duyệt có thể:

```text
Is Duplicate: true
Duplicate Of: FACE_00003
```

Record duplicate:

- Vẫn tồn tại trong GT Draft.
- Không được tính là event độc lập trong GT Final.
- Vẫn giữ để truy vết.

---

## 17.4. Stable Event Key

```text
event_key = hash(
    job_id
    + source_file_hash
    + camera_id
    + object_type
    + track_id
    + start_frame
)
```

Mục đích:

- Tránh export trùng.
- Đảm bảo idempotency.
- Tránh tạo lại cùng event nhiều lần.

---

# 18. Ngăn sinh case không tồn tại trong video

Các nguyên nhân có thể:

- Model false positive.
- OCR đọc chữ trên tường.
- Cache của job cũ.
- Mapping sai video.
- Worker ghi nhầm thư mục.
- Frame number sai.
- Timestamp sai.
- Crop lấy từ frame khác.
- Hai job dùng chung output.

## Evidence Validator

Trước khi ghi record hoặc export:

1. Kiểm tra source file tồn tại.
2. Kiểm tra frame number hợp lệ.
3. Đọc lại frame.
4. Kiểm tra bbox hợp lệ.
5. Crop lại từ source frame.
6. Kiểm tra crop không rỗng.
7. Kiểm tra timestamp trong duration.
8. Kiểm tra job ID.
9. Kiểm tra source hash.
10. Kiểm tra path thuộc đúng job.

Nếu không hợp lệ:

```text
Evidence Status: INVALID_EVIDENCE
```

Không đưa record đó vào GT Final.

---

# 19. Nhãn dữ liệu

## Face Labels

```text
KNOWN_FACE
UNKNOWN_FACE
FACE_CLEAR
FACE_BLUR
FACE_OCCLUDED
FACE_MASKED
FACE_SIDE_VIEW
FACE_BACK_VIEW
FACE_TOO_SMALL
FACE_TOO_DARK
FACE_OVEREXPOSED
FALSE_DETECTION
DUPLICATE
LOW_EVIDENCE
MISSED_FACE
MANUAL_ADDITION
```

## Plate Labels

```text
PLATE_READABLE
PLATE_UNREADABLE
PLATE_BLUR
PLATE_OCCLUDED
PLATE_TOO_SMALL
PLATE_PARTIAL
NO_PLATE_VISIBLE
FALSE_DETECTION
DUPLICATE
LOW_EVIDENCE
MISSED_PLATE
MANUAL_ADDITION
```

## Verify Status

```text
UNVERIFIED
IN_REVIEW
VERIFIED
REJECTED
DISCARDED
NEEDS_SECOND_REVIEW
```

Trong MVP có thể sử dụng:

```text
UNVERIFIED
VERIFIED
DISCARDED
```

---

# 20. Excel Output

## 20.1. Sheet `GT_Draft`

Các cột:

| Cột | Nội dung |
|---|---|
| RecordID | ID record |
| JobID | ID job |
| SourceFile | File nguồn |
| CameraID | Camera |
| ObjectType | FACE/PLATE |
| TrackID | Tracking ID |
| StartTimestamp | Thời gian bắt đầu |
| EndTimestamp | Thời gian kết thúc |
| BestTimestamp | Thời gian frame tốt nhất |
| StartFrame | Frame bắt đầu |
| EndFrame | Frame kết thúc |
| BestFrame | Frame tốt nhất |
| FullFrame | Ảnh toàn cảnh |
| CropImage | Ảnh crop |
| PredictedText | Prediction |
| PredictedID | Face ID |
| DetectionConfidence | Confidence detection |
| RecognitionConfidence | Recognition/OCR confidence |
| QualityScore | Điểm chất lượng |
| Classification | Nhãn |
| GTText | Ground Truth |
| GTID | Ground Truth ID |
| VerifyStatus | Trạng thái |
| IsDuplicate | Có phải duplicate |
| DuplicateOf | Track gốc |
| Discard | Loại bỏ |
| Note | Ghi chú |
| ModelVersion | Model version |
| ConfigVersion | Config version |

---

## 20.2. Sheet `GT_Final`

Chỉ gồm record:

```text
VerifyStatus = VERIFIED
AND Discard = FALSE
AND IsDuplicate = FALSE
AND EvidenceValid = TRUE
```

---

## 20.3. Sheet `Summary`

- Total frames.
- Total tracks.
- Total face tracks.
- Total plate tracks.
- Verified.
- Unverified.
- Discarded.
- Duplicate.
- False Positive.
- Missed Case.
- Unknown.
- Low Confidence.
- Processing Time.
- Verification Time.

---

## 20.4. Sheet `Config`

- Camera.
- ROI.
- Sample rate.
- Processing mode.
- Threshold.
- Model version.
- Video hash.
- Processed date.
- Created by.
- Verified by.

---

# 21. Development Workflow

## Phase 0 — Setup Codebase

Mục tiêu:

```text
Một lệnh có thể chạy:
PostgreSQL
Redis
Backend
Worker
Frontend
```

Công việc:

1. Khởi tạo repository.
2. Tạo Docker Compose.
3. Khởi tạo FastAPI.
4. Khởi tạo React.
5. Kết nối PostgreSQL.
6. Setup SQLAlchemy.
7. Setup Alembic.
8. Kết nối Redis.
9. Setup RQ Worker.
10. Tạo health endpoint.
11. Tạo `.env.example`.
12. Tạo Makefile.
13. Setup test.
14. Setup lint.
15. Setup pre-commit.

---

## Phase 1 — Evidence Baseline

Ưu tiên:

```text
Đọc đúng video
→ Lấy đúng frame
→ Tính đúng timestamp
→ Validate đúng bbox
→ Crop đúng đối tượng
→ Không sinh dữ liệu không có thật
```

Công việc:

- Video Reader.
- Metadata Reader.
- Frame Sampler.
- Timestamp Calculator.
- Bounding-box Validator.
- Crop Generator.
- Evidence Validator.
- File Hash.
- Job Isolation.

---

## Phase 2 — Detection Baseline

- Plate Detector.
- ROI Filter.
- Full Frame Saving.
- Crop Saving.
- Confidence.
- Raw Excel Export.

---

## Phase 3 — Tracking và Recognition

- ByteTrack hoặc DeepSORT.
- TrackID.
- Start/End.
- Best Frame.
- Plate OCR.
- Multi-frame Voting.
- Duplicate Detection.

---

## Phase 4 — Human Verification

- Review Workspace.
- GT Form.
- Verify.
- Discard.
- Duplicate.
- Missed Case.
- GT Final Export.

---

# 22. Kế hoạch 3 Sprint

## Sprint 1 — Setup và Detection Baseline

### Mục tiêu

```text
Video/Tập ảnh
→ Đọc frame
→ Detect Face/Plate
→ Lưu bbox
→ Lưu timestamp
→ Lưu full frame và crop
→ Xuất Excel thô
```

### Công việc

#### Setup

- Repository.
- Docker Compose.
- PostgreSQL.
- Redis.
- FastAPI.
- React.
- SQLAlchemy.
- Alembic.
- RQ Worker.
- Health check.
- Makefile.
- CI baseline.

#### Vision Baseline

- Video reader.
- Timestamp calculator.
- Frame sampling.
- ROI filter.
- Face detector.
- Plate detector.
- BBox validator.
- Crop generator.
- Evidence validator.

#### Frontend Baseline

- App shell.
- Sidebar MVP.
- Login.
- Dashboard.
- New Job.
- Progress.
- Raw result table.

#### Export Baseline

- Excel.
- Timestamp.
- Frame.
- BBox.
- Full-frame path.
- Crop path.
- Confidence.

### Definition of Done

- Clone repository chạy được bằng Docker Compose.
- PostgreSQL migration chạy được.
- Redis và worker kết nối được.
- Upload được video.
- Timestamp đúng.
- Frame đúng.
- Crop đúng.
- Không export record không có evidence.
- Có unit test cho timestamp, bbox và crop.
- Có file Excel baseline.

---

## Sprint 2 — Tracking, Recognition và GT Draft

### Mục tiêu

```text
Detection
→ Tracking
→ Best Frame
→ Recognition
→ Duplicate Handling
→ GT Draft
```

### Công việc

- TrackID.
- Start/End Frame.
- Start/End Timestamp.
- Best-frame Selection.
- Face Recognition.
- Known/Unknown.
- Plate OCR.
- Multi-frame Voting.
- Duplicate Detection.
- GT Draft.
- Summary sheet.
- Config sheet.
- Review Workspace baseline.

### Definition of Done

- Một lượt người chủ yếu tạo một TrackID.
- Có best frame.
- Có Known/Unknown.
- Có OCR biển số.
- Có duplicate flag.
- Có GT fields.
- Có Verify Status.
- Export được GT Draft.

---

## Sprint 3 — Verification và Bàn giao

### Mục tiêu

Tool có thể dùng để QA tạo GT Final.

### Công việc

- Search.
- Filter.
- Sort.
- Full frame.
- Crop.
- Nearby frames.
- GT input.
- Verify.
- Discard.
- Duplicate.
- Manual missed case.
- Export GT Final.
- Test trên video thực tế.
- Fix lỗi.
- User Guide.
- Developer Guide.
- Demo.
- Test Report.

### Definition of Done

- Verify được trên web.
- Sửa GT được.
- Discard được.
- Duplicate được.
- Bổ sung missed case được.
- Export GT Final được.
- Có test report.
- Có tài liệu cài đặt.
- Có tài liệu sử dụng.
- Có danh sách known limitations.

---

# 23. Makefile

Repository nên có các command:

```makefile
.PHONY: build up down restart logs migrate seed test lint format clean

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

restart:
	docker compose restart

logs:
	docker compose logs -f

migrate:
	docker compose run --rm backend alembic upgrade head

seed:
	docker compose run --rm backend python scripts/seed_data.py

test:
	docker compose run --rm backend pytest
	docker compose run --rm frontend npm run test

lint:
	docker compose run --rm backend ruff check .
	docker compose run --rm frontend npm run lint

format:
	docker compose run --rm backend ruff format .
	docker compose run --rm frontend npm run format

clean:
	docker compose down
	find . -type d -name "__pycache__" -exec rm -rf {} +
```

Sử dụng:

```bash
make build
make migrate
make up
make test
```

---

# 24. Database Migration

Tạo migration:

```bash
docker compose run --rm backend \
  alembic revision --autogenerate -m "add_tracks"
```

Chạy migration:

```bash
docker compose run --rm backend alembic upgrade head
```

Rollback một migration:

```bash
docker compose run --rm backend alembic downgrade -1
```

Không thay đổi trực tiếp database production nếu thay đổi đó cần được áp dụng cho các môi trường khác.

---

# 25. Testing

## 25.1. Unit Test

Ưu tiên:

- Timestamp calculation.
- Frame extraction.
- Bounding-box validation.
- Crop reproducibility.
- Stable event key.
- Label validation.
- Excel field mapping.

Chạy:

```bash
docker compose run --rm backend pytest tests/unit
```

---

## 25.2. Integration Test

- PostgreSQL repository.
- Job creation.
- Redis queue.
- Worker execution.
- Evidence storage.
- GT update.
- Export.

Chạy:

```bash
docker compose run --rm backend pytest tests/integration
```

---

## 25.3. Frontend Test

```bash
docker compose run --rm frontend npm run test
```

Lint:

```bash
docker compose run --rm frontend npm run lint
```

---

## 25.4. End-to-End Test

Luồng:

```text
Login
→ Create Job
→ Upload Video
→ Start Processing
→ Review Record
→ Verify
→ Mark Duplicate
→ Add Missed Case
→ Export GT Final
```

---

## 25.5. Test case quan trọng

### Face

1. Một người đi qua liên tục.
2. Người dừng lâu.
3. Người bị che tạm thời.
4. Hai người đi cạnh nhau.
5. Hai người giao nhau.
6. Người đội mũ.
7. Người đeo khẩu trang.
8. Người quay nghiêng.
9. Người quay lưng.
10. Mặt quá nhỏ.
11. Mặt quá tối.
12. Không có mặt nhưng model detect nhầm.
13. Cùng người xuất hiện hai lượt khác nhau.

### Plate

1. Một xe đi qua liên tục.
2. Xe dừng lâu.
3. Biển bị mờ.
4. Biển bị che.
5. Biển hai dòng.
6. OCR sai một ký tự.
7. Hai biển gần giống.
8. Chữ trên tường bị detect nhầm.
9. Cùng xe xuất hiện hai lượt.

### System

1. Chạy lại cùng video.
2. Chạy hai job đồng thời.
3. Hủy job.
4. Worker lỗi.
5. PostgreSQL mất kết nối.
6. Redis mất kết nối.
7. Export nhiều lần.
8. Job đọc crop của job khác.
9. Frame number vượt giới hạn.
10. Crop path không tồn tại.
11. Video variable frame rate.
12. Record thiếu evidence.

---

# 26. Coding Rules

## Backend

- Public function phải có type hints.
- Controller không chứa business logic.
- Business logic nằm trong service.
- Database query nằm trong repository.
- Không viết SQL trực tiếp trong route.
- Không hard-code model path.
- Không hard-code threshold.
- Không hard-code ROI.
- Không sử dụng global mutable state.
- Mọi job phải có `job_id`.
- Mọi log phải chứa `job_id`.
- Record phải qua Evidence Validator trước khi export.

---

## Frontend

- Không gọi API trực tiếp trong page component.
- API đặt trong `services`.
- Type đặt trong `types`.
- Form dùng React Hook Form và Zod.
- Không hard-code label trong component.
- Table hỗ trợ pagination.
- Ảnh sử dụng lazy loading.
- Review Workspace ưu tiên split view.
- Action nguy hiểm phải có confirmation.

---

## Vision

Các interface bắt buộc:

```python
from typing import Protocol


class Detector(Protocol):
    def detect(self, frame):
        ...


class Tracker(Protocol):
    def update(self, detections, frame):
        ...


class Recognizer(Protocol):
    def recognize(self, crop):
        ...


class QualityScorer(Protocol):
    def score(self, crop, metadata):
        ...


class Deduplicator(Protocol):
    def deduplicate(self, tracks):
        ...


class EvidenceValidator(Protocol):
    def validate(self, record):
        ...
```

Vision pipeline không được phụ thuộc trực tiếp vào FastAPI.

---

# 27. Logging

Log cần có dạng structured log.

Ví dụ:

```json
{
  "level": "INFO",
  "message": "Track created",
  "job_id": "JOB_20260721_0001",
  "source_file": "lane9.mp4",
  "track_id": "FACE_00012",
  "frame_number": 1250
}
```

Không log:

- Ảnh base64.
- Face embedding.
- Password.
- Access token.
- Thông tin cá nhân không cần thiết.

---

# 28. Data Safety và bảo mật

Dữ liệu khuôn mặt là dữ liệu nhạy cảm.

Không được commit:

- Video thật.
- Ảnh gallery thật.
- Embedding thật.
- Model weight có bản quyền.
- File `.env`.
- Database dump thật.
- Excel GT chứa dữ liệu cá nhân thật.

Dữ liệu test trong Git phải là:

- Dữ liệu tổng hợp.
- Dữ liệu đã làm mờ.
- Dữ liệu đã ẩn danh.
- Dữ liệu được phép sử dụng.

Production cần:

- HTTPS.
- Password hashing.
- Token expiration.
- File validation.
- Role-based access.
- Export permission.
- Audit log.
- Data retention policy.
- Secure object storage.
- Backup policy.

---

# 29. Git Workflow

## Branch

```text
main
develop
feature/*
fix/*
docs/*
```

Ví dụ:

```text
feature/setup-fastapi
feature/video-reader
feature/face-detector
feature/gt-review
fix/timestamp-offset
docs/update-readme
```

## Commit

Sử dụng conventional commits:

```text
feat: add video upload API
fix: correct timestamp calculation
docs: update setup guide
test: add crop validation tests
refactor: separate job repository
chore: update Docker configuration
```

## Pull Request

PR cần có:

- Mô tả thay đổi.
- Lý do thay đổi.
- Cách test.
- Screenshot nếu thay đổi UI.
- Migration nếu thay đổi database.
- Known limitations.
- Checklist.

---

# 30. Tài liệu chi tiết

Đọc theo thứ tự:

1. `README.md`
2. `AGENTS.md` hoặc `CLAUDE.md`
3. `docs/00-getting-started.md`
4. `docs/01-product-overview.md`
5. `docs/02-mvp-scope.md`
6. `docs/03-functional-specification.md`
7. `docs/04-system-architecture.md`
8. `docs/05-database-schema.md`
9. `docs/06-face-pipeline.md`
10. `docs/07-plate-pipeline.md`
11. `docs/08-tracking-deduplication.md`
12. `docs/09-api-specification.md`
13. `docs/10-frontend-specification.md`
14. `docs/11-excel-output.md`
15. `docs/12-test-plan.md`
16. `docs/13-security-and-data.md`
17. `docs/14-deployment-guide.md`
18. `docs/15-user-guide.md`
19. `docs/16-sprint-plan.md`
20. `docs/17-known-limitations.md`

---

# 31. Hướng dẫn cho AI Coding Agent

Trước khi viết code, coding agent phải đọc:

```text
1. README.md
2. AGENTS.md hoặc CLAUDE.md
3. docs/00-getting-started.md
4. docs/02-mvp-scope.md
5. docs/04-system-architecture.md
6. docs/05-database-schema.md
7. docs/16-sprint-plan.md
```

Không tự ý triển khai toàn bộ dự án cùng lúc.

Thứ tự thực hiện:

```text
Phase 0 — Setup Codebase
Phase 1 — Evidence Baseline
Phase 2 — Detection Baseline
Phase 3 — Tracking and Recognition
Phase 4 — Human Verification
```

Nhiệm vụ tiếp theo sau khi Phase 0 hoàn tất:

```text
Thực hiện Phase 1 — Evidence Baseline cho biển số.

Yêu cầu trước khi tích hợp detector/OCR:
- Đọc metadata và PTS của video mẫu.
- Lấy frame/timestamp đúng cả với video VFR.
- Tính SHA-256 file nguồn.
- Validate bbox trước khi crop.
- Lưu full frame và crop theo từng job.
- Không cho phép evidence tham chiếu chéo job.
- Có test bằng video mẫu hiện có.
```

---

# 32. Troubleshooting

## PostgreSQL không kết nối được

```bash
docker compose logs postgres
docker compose ps
```

Kiểm tra:

```env
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
```

Bên trong Docker không dùng `localhost` để kết nối PostgreSQL container.

---

## Redis không kết nối được

```bash
docker compose logs redis
```

Kiểm tra:

```env
REDIS_URL=redis://redis:6379/0
```

---

## Backend không chạy

```bash
docker compose logs backend
```

Kiểm tra:

- `.env`.
- `DATABASE_URL`.
- Migration.
- Python dependencies.
- Port 8000.

---

## Frontend không chạy

```bash
docker compose logs frontend
```

Kiểm tra:

- `VITE_API_BASE_URL`.
- Port 5173.
- Node modules.
- CORS backend.

---

## Worker không nhận job

```bash
docker compose logs worker
```

Kiểm tra:

- Redis URL.
- Queue name.
- Worker process.
- Job serialization.
- Storage volume.

---

## Migration lỗi

```bash
docker compose run --rm backend alembic current
docker compose run --rm backend alembic history
```

Có thể reset local database:

```bash
docker compose down -v
docker compose up -d postgres redis
docker compose run --rm backend alembic upgrade head
```

> Chỉ sử dụng reset database trong môi trường development.

---

## Backend không thấy model

Kiểm tra:

```env
MODEL_ROOT=/app/models
```

Kiểm tra volume trong `docker-compose.yml`.

---

## Không ghi được file storage

```bash
mkdir -p storage/uploads
mkdir -p storage/jobs
mkdir -p storage/exports
```

Kiểm tra quyền ghi của container.

---

## Timestamp bị lệch

Kiểm tra:

- FPS.
- Variable Frame Rate.
- Presentation Timestamp.
- FFmpeg metadata.
- Frame sampling logic.

Không mặc định mọi video đều có Constant Frame Rate.

---

## Crop không đúng frame

Kiểm tra:

- JobID.
- Source hash.
- Frame number.
- Bounding box.
- File path.
- Evidence Validator.
- Output folder isolation.

---

# 33. Project Status

Trạng thái:

```text
Prototype / MVP Development
```

Ưu tiên hiện tại:

```text
Setup Codebase
→ Evidence Pipeline
→ Plate Detection
→ Excel Baseline
→ Tracking
→ Plate OCR và multi-frame voting
→ Review Workspace
→ GT Final Export
```

---

# 34. Tiêu chí thành công của MVP

MVP được coi là thành công khi:

1. Người dùng upload được video hoặc tập ảnh.
2. Tool phát hiện được khuôn mặt hoặc biển số.
3. Mỗi record có frame và timestamp thật.
4. Crop truy xuất được từ frame gốc.
5. Có TrackID.
6. Có best frame.
7. Có prediction và confidence.
8. Có GT field.
9. Người dùng sửa và verify được.
10. Người dùng discard được false detection.
11. Người dùng đánh dấu được duplicate.
12. Người dùng bổ sung được missed case.
13. Export được GT Draft.
14. Export được GT Final.
15. Không export record thiếu evidence.
16. Không trộn dữ liệu giữa các job.
17. Dev mới clone repository có thể chạy theo README.

---

# 35. Câu chốt dự án

> DatVision GT không nhằm thay thế con người trong việc xác nhận Ground Truth.  
> Hệ thống giúp con người tìm đúng frame, gom đúng đối tượng, lưu đúng bằng chứng và kiểm duyệt dữ liệu nhanh hơn.

> **No evidence, no record.**
