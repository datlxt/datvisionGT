# Hướng dẫn finetune OCR biển số VN (fast-plate-ocr / CCT)

Mục tiêu: sửa các lỗi model **đọc sai ký tự** trên biển VN xuống cấp mà hậu xử lý không
cứu được — ví dụ **D→J** (tem tròn đè chữ), **H→W/N** (chói/mòn), **Z↔2, E↔F, 2↔7, 3↔9**.
Đây là giới hạn của model OCR, chỉ finetune trên đúng dữ liệu domain mới sửa gốc.

Model hiện tại: `cct-xs-v2-global-model` (fast-plate-ocr), input **128×64 RGB**, alphabet
`0-9 A-Z _`, tối đa 10 ký tự (xem `models/plate-ocr/plate_config.yaml`).

---

## Bước 0 — Điều kiện: đủ dữ liệu đã duyệt

Finetune chỉ tốt khi có đủ **GT đã VERIFIED**, đặc biệt **case khó** (tem, chói, mòn, bẩn).
- Mỗi biển "Xác nhận GT" trong tool = 1 nhãn đúng.
- Nên tích lũy tới **≥ vài nghìn crop** (xuất từ nhiều lane). Với ~64 biển verified hiện tại,
  script dưới sinh ~1.000 crop (nhiều frame/biển) — đủ để **thử nghiệm**, nhưng để cải thiện
  chắc chắn nên gom thêm, ưu tiên các biển model đang đọc sai.

## Bước 1 — Xuất training-set từ GT đã duyệt

Script tái trích **nhiều frame/biển** (không chỉ 1 best-crop) để bắt được đủ biến thể
chói/tem/mờ, tất cả mang **nhãn người đã xác nhận**. Định dạng khớp fast-plate-ocr:
thư mục `images/` + `train.csv` / `val.csv` với 2 cột `image_path,plate_text`.

```bash
# Chạy trong container worker (có sẵn model + DB). Tiền tố MSYS_NO_PATHCONV=1 để Git Bash
# trên Windows không đổi đường dẫn /app.
MSYS_NO_PATHCONV=1 docker compose exec worker \
  python -m scripts.export_ocr_trainset \
    --output-root /app/storage/datasets/vn-ocr-finetune \
    --frames-per-plate 20 --sample-every-ms 200 --val-ratio 0.15
```

Kết quả nằm ở `storage/datasets/vn-ocr-finetune/` (đã gitignore). Kiểm tra vài ảnh + `train.csv`.

> Mẹo: giữ 1 lane **riêng** làm val (đừng trộn cùng lane vào cả train lẫn val) để đo trung thực.

## Bước 2 — Chuẩn bị Colab (GPU T4 free là đủ cho model XS)

```python
!pip install "fast-plate-ocr[train]" onnx onnxslim
```

Lấy **model gốc** (weights + config kiến trúc) để finetune — dùng model hub của thư viện:

```python
from huggingface_hub import snapshot_download
base = snapshot_download("ankandrew/cct-xs-v2-global-model")  # kiểm tra id tại trang HF của fast-plate-ocr
print(base)   # chứa: <model>.keras, model config yaml, plate config yaml
```

Tải `storage/datasets/vn-ocr-finetune/` lên Colab (Drive hoặc upload zip), rồi sửa `image_path`
trong CSV thành đường dẫn tuyệt đối hoặc **chạy train từ thư mục dataset** để `images/...` khớp.

## Bước 3 — Augmentation nhắm đúng lỗi của bạn

Tạo file augmentation (Albumentations) mô phỏng đúng các suy biến gây lỗi:

```python
import albumentations as A
aug = A.Compose([
    A.CoarseDropout(max_holes=2, max_height=18, max_width=18, fill_value=0, p=0.4),  # tem/che chữ (D→J)
    A.RandomBrightnessContrast(0.3, 0.3, p=0.6),        # chói / thiếu sáng
    A.RandomShadow(p=0.2), A.RandomSunFlare(src_radius=60, p=0.15),  # chói cam đèn hậu (H→W)
    A.MotionBlur(blur_limit=5, p=0.3), A.GaussNoise(p=0.2),          # mờ chuyển động / nhiễu
    A.Perspective(scale=(0.02, 0.06), p=0.3), A.Rotate(limit=6, p=0.3),
])
A.save(aug, "aug.yaml", data_format="yaml")
```

## Bước 4 — Finetune (từ weights gốc, KHÔNG train từ đầu)

```bash
fast-plate-ocr train \
  --model-config-file  {base}/model_config.yaml \
  --plate-config-file  {base}/plate_config.yaml \
  --annotations        vn-ocr-finetune/train.csv \
  --val-annotations    vn-ocr-finetune/val.csv \
  --weights-path       {base}/cct-xs-v2.keras \
  --augmentation-path  aug.yaml \
  --lr 1e-4 --batch-size 64 --epochs 60 \
  --validate-dataset warn \
  --output-dir ./trained_models
```

- `--weights-path` = **finetune** (nạp weights gốc, `skip_mismatch=True`). LR nhỏ (1e-4) để không
  phá kiến thức cũ. Theo dõi `plate_acc` / CER trên val; dừng khi val không cải thiện.
- Kết quả: `trained_models/best.keras`.

## Bước 5 — Export ONNX

```bash
fast-plate-ocr export \
  --model ./trained_models/best.keras --format onnx \
  --plate-config-file {base}/plate_config.yaml \
  --onnx-data-format channels_first --save-dir ./export
```

## Bước 6 — Deploy + đo lại

1. Tải `export/*.onnx` về, thay `models/plate-ocr/model.onnx` (giữ nguyên `plate_config.yaml`
   nếu alphabet/kích thước không đổi). Cập nhật version trong `pipeline.py` (`fast-plate-ocr-1.1.0`).
2. **Đo baseline vs sau finetune** bằng evaluator có sẵn:
   ```bash
   docker compose exec worker python -m scripts.evaluate_benchmark <dataset/manifest>
   ```
3. Chạy lại 1-2 lane có GT, so CER/accuracy **trước vs sau** — nhất là các case D→J, H→W.

---

## Lưu ý / cạm bẫy
- **Alphabet + kích thước input phải khớp** `plate_config.yaml` cũ, nếu không adapter sẽ lệch.
- **Đừng overfit 1 camera**: trộn nhiều lane; giữ 1 lane held-out làm val.
- **Cân bằng dữ liệu**: đừng để 1 biển chiếm quá nhiều crop (giới hạn `--frames-per-plate`).
- Finetune **OCR** chỉ sửa **đọc ký tự**. Lỗi **detector bắt nhầm thẻ** cần finetune **detector**
  (khác), không nằm trong guide này.
- Sau mỗi vòng: gom thêm case model vẫn sai → finetune tiếp (vòng lặp cải thiện).
