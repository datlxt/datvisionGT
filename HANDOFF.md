# DatVision GT — Hướng dẫn bàn giao & chạy (Quickstart)

Công cụ **tạo & kiểm duyệt Ground Truth biển số từ video**. Toàn bộ đóng gói bằng **Docker Compose** —
người nhận chỉ cần Docker là chạy được, không cần cài Python/Node thủ công.

---

## 1. Yêu cầu máy
- **Docker Desktop** (Windows / macOS) hoặc **Docker Engine + Compose** (Linux).
- RAM khuyến nghị **≥ 8 GB**, ổ trống **≥ 10 GB** (ảnh bằng chứng + DB).
- Không cần GPU (chạy CPU).

## 2. Nội dung gói bàn giao
```
datvisionGT/
├── backend/            # mã nguồn API + worker
├── frontend/           # mã nguồn giao diện
├── models/             # ⚠️ TRỌNG SỐ AI (.onnx) — BẮT BUỘC phải có, gửi kèm
├── compose.yaml        # cấu hình Docker (dev/local)
├── compose.production.yaml
├── .env.example        # mẫu cấu hình (KHÔNG chứa mật khẩu/khoá thật)
├── docs/               # tài liệu chi tiết
└── HANDOFF.md          # file này
```
> **KHÔNG có file `.env`** trong gói (đó là bí mật của người gửi). Người nhận **tự tạo** ở bước 3.
> **`models/` không nằm trong git** — nếu clone từ GitHub sẽ THIẾU, phải copy thư mục `models/` kèm theo.

## 3. Cấu hình (1 lần)
```bash
cp .env.example .env
```
Mở `.env`, sửa tối thiểu:
- **`POSTGRES_PASSWORD`** → đặt mật khẩu mới (đừng để `change_me_before_deploy`).
- **`DATABASE_URL`** → thay đúng mật khẩu vừa đặt (phần `:...@postgres`).

Để **chạy hoàn toàn offline** (mặc định): giữ `CLOUD_OCR_ENABLED=false` — không cần khoá API nào.
Muốn bật **AI đối chiếu (2/3 đồng thuận)**: đặt `CLOUD_OCR_ENABLED=true` và điền `OPENAI_API_KEY`,
`QWEN_API_KEY` (khoá của người nhận).

## 4. Chạy
```bash
# build image (lần đầu ~vài phút)
docker compose build

# bật DB + Redis, chạy migration tạo bảng (1 lần)
docker compose up -d postgres redis
docker compose --profile tools run --rm migrate

# bật toàn bộ hệ thống
docker compose up -d
```

## 5. Truy cập
- Mở trình duyệt: **http://localhost:5173**
- Kiểm tra sức khoẻ API: **http://localhost:5173/api/v1/health**
- Trong mạng nội bộ, người khác vào bằng **http://IP-máy-chủ:5173**.

## 6. Dừng / xem log / gỡ
```bash
docker compose ps              # trạng thái các service
docker compose logs -f worker  # xem log xử lý video
docker compose down            # dừng (GIỮ dữ liệu)
docker compose down -v         # dừng + XOÁ sạch DB/Redis volume (cẩn thận!)
```

## 7. Lưu ý quan trọng
- **Dữ liệu** nằm ở thư mục `./storage` (video, ảnh bằng chứng) và volume PostgreSQL — **sao lưu 2 chỗ này**.
- **Chưa có đăng nhập.** Trước khi mở cho nhiều người ngoài, thêm `basic_auth` ở gateway (Caddy) và đổi mật khẩu DB.
- **Xử lý song song nhiều video:** chạy thêm worker → `docker compose up -d --scale worker=3` (giới hạn theo số nhân CPU).
- Chi tiết triển khai máy chủ thật: xem `docs/14-deployment-guide.md`.

## 8. Trục trặc thường gặp
| Hiện tượng | Cách xử lý |
|---|---|
| Backend báo thiếu model | Kiểm tra thư mục `models/` có đủ `vehicle/`, `plate-detector/`, `plate-ocr/` |
| Cổng 5173 bận | Đổi `HTTP_PORT` trong `.env` rồi `docker compose up -d` lại |
| Xử lý video "Thất bại" | Xem `docker compose logs worker`; thử `docker compose restart worker` |
| Trang trắng | Đợi `docker compose ps` tất cả **healthy**, rồi tải lại trang |

---

## 9. Cấu trúc mã nguồn (để phát triển tiếp)
```
backend/app/
├── api/       # endpoint FastAPI (jobs, results, condense, export, ground-truth...)
├── workers/   # pipeline.py (xử lý video) · missed.py (soát bỏ sót) · condense.py (cắt video)
├── vision/    # AI: plate/ (YOLOX, YOLOv9, OCR, tracking, gộp lượt) · media/ (extract, condense)
├── export/    # xuất Excel: gt_final.py, plate_report.py
├── db/        # session.py (kết nối DB) · migrations Alembic
├── models/    # ORM (định nghĩa bảng)
├── core/      # config.py — MỌI cấu hình/biến môi trường
└── main.py    # khởi tạo app
frontend/src/
├── pages/     # ReviewPage (kiểm duyệt) · CondensePage (cắt) · CreateJobPage · ExportsPage...
├── components/# thành phần dùng chung
└── lib/       # gọi API
```
**Logic cốt lõi cần biết:**
- Đọc biển + **gộp lượt / lọc trùng**: `backend/app/vision/plate/domain.py` (`consolidate_vehicle_events`)
- Xử lý video (pipeline chính): `backend/app/workers/pipeline.py`
- Soát bỏ sót: `backend/app/workers/missed.py`
- Màn kiểm duyệt GT: `frontend/src/pages/ReviewPage.tsx`
- Cấu hình & ngưỡng: `backend/app/core/config.py` + `.env`

## 10. Phát triển tiếp (sau khi sửa code)
```bash
docker compose build            # build lại toàn bộ
docker compose up -d
# hoặc chỉ 1 service:
docker compose build backend && docker compose up -d backend
```
Frontend là giao diện tĩnh, do service **gateway** build & phục vụ → sửa `frontend/` xong `build gateway`.

## 11. Tài liệu để hiểu sâu & kế thừa (đọc theo thứ tự)
1. **`README.md`** — giới thiệu đầy đủ, luồng nghiệp vụ & công nghệ, Quick Start chi tiết.
2. **`CLAUDE.md`** — ⭐ hợp đồng làm việc + **QUY TẮC KHÔNG ĐƯỢC PHÁ** (đọc trước khi sửa gì).
3. **`docs/`**: `00-getting-started` · `05-database-schema` · `14-deployment-guide` ·
   `15-evidence-and-benchmark` · `16-motorcycle-alpr-mvp` · `18-lane9-gt-export-contract` ·
   `19-ocr-finetune-guide` (+ `colab_finetune_vn_ocr.ipynb` để fine-tune OCR).

## 12. ⚠️ Quy tắc nghiệp vụ KHÔNG được phá (tóm tắt — chi tiết ở CLAUDE.md)
- **Dự đoán ≠ GT:** GT chỉ tự điền khi **≥2/3 nguồn đọc độc lập khớp**; còn lại **người quyết**.
- **1 lượt xe = 1 dòng** (gộp mảnh của cùng xe; xe không biển vẫn là một sự kiện).
- **Không bằng chứng → không ghi nhận** (mỗi dòng truy ngược video · frame · crop).
- **Không tự ý đổi bảng/cột CSDL** chỉ để khớp giao diện.
