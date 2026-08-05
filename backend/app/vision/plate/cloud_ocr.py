"""Cloud OCR cross-check: a SECOND, independent reader for a plate crop.

The local CCT-XS-v2 model reads every plate first. This module asks an OpenAI vision model
to read the SAME crop again and to judge the plate-quality category. Two independent readers
rarely make the identical mistake, so a disagreement is a strong "a human must look" signal —
much stronger than one model's confidence.

Design guarantees:
- Runs only in a separate post-processing step, never inside the offline video worker.
- Network failures / timeouts never raise: the caller gets ``None`` and the plate is simply
  marked "not cross-checked" instead of blocking the pipeline.
- Uses only the standard library (urllib) so no new dependency is pinned.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from app.core.config import Settings

_MAX_RETRIES = 3

# Must stay in sync with the reviewer dropdown (_QUALITY_OPTIONS in export/plate_report.py).
_QUALITY_CATEGORIES = (
    "Biển đẹp bình thường",
    "Biển bẩn",
    "Biển cũ, xước, mờ",
    "Biển bị che vật lý",
    "Biển bị dán (icon, decal, trang trí...)",
    "Biển bị chói sáng",
    "Biển giả mạo (che 1 vài số, dán biển khác lên...)",
    "Biển biến dạng vật lý (cong, vênh)",
    "Biển bóng của vật thể che khuất",
    "Xe không biển",
)

# The rear-camera plate crops are tiny (≈117px wide on average), which a vision model reads and
# classifies poorly — the same crop upscaled is much easier for it. Enlarge small crops with a
# high-quality filter before sending so the AI sees the plate the way a human would when zoomed in.
_AI_MIN_CROP_WIDTH = 360


def _upscale_for_ai(image_bytes: bytes) -> bytes:
    """Upscale a small plate crop to a legible width (LANCZOS). Never raises — returns the
    original bytes on any failure so the cross-check can't break over image handling."""

    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as source:
            rgb = source.convert("RGB")
            if rgb.width >= _AI_MIN_CROP_WIDTH:
                return image_bytes
            height = max(1, round(rgb.height * _AI_MIN_CROP_WIDTH / rgb.width))
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            rgb = rgb.resize((_AI_MIN_CROP_WIDTH, height), resampling)
            buffer = io.BytesIO()
            rgb.save(buffer, format="JPEG", quality=92)
            return buffer.getvalue()
    except Exception:
        return image_bytes


# Clear per-label criteria so the model doesn't confuse look-alike defects — the common mistake
# is calling an over-bright / washed-out plate "bẩn" (dirty) instead of "chói sáng" (glare).
_QUALITY_CRITERIA = (
    "- 'Biển bị chói sáng': có vùng TRẮNG XOÁ / loá do đèn hoặc phản chiếu, ký tự mất nét vì QUÁ"
    " SÁNG. Đây KHÔNG phải bẩn.\n"
    "- 'Biển bẩn': có bùn/đất/vết bẩn TỐI MÀU bám lên mặt biển.\n"
    "- 'Biển cũ, xước, mờ': sơn tróc, trầy xước, phai màu, mờ chung chung"
    " (không do sáng hay bẩn).\n"
    "- 'Biển bị che vật lý': có VẬT (tem, tay, dây, đồ) che khuất một phần ký tự.\n"
    "- 'Biển bị dán (icon, decal, trang trí...)': có hình dán/trang trí đè lên biển.\n"
    "- 'Biển giả mạo (che 1 vài số, dán biển khác lên...)': cố ý che/sửa số hoặc dán số khác.\n"
    "- 'Biển biến dạng vật lý (cong, vênh)': biển bị cong / méo / gãy.\n"
    "- 'Biển bóng của vật thể che khuất': bóng đổ của vật thể phủ lên biển.\n"
    "- 'Xe không biển': không có biển số.\n"
    "- 'Biển đẹp bình thường': rõ nét, không khiếm khuyết đáng kể.\n"
)
_PROMPT = (
    "Bạn là chuyên gia đọc biển số xe máy Việt Nam từ ảnh crop biển.\n"
    "1) Đọc CHÍNH XÁC ký tự (chỉ A-Z và 0-9, bỏ dấu gạch/chấm, gộp 2 dòng thành 1 chuỗi; "
    "Đ đọc thành D). Không đọc được thì để plate rỗng.\n"
    "2) Phân loại chất lượng: chọn ĐÚNG MỘT nhãn ứng với KHIẾM KHUYẾT NỔI BẬT NHẤT,"
    " theo tiêu chí:\n"
    + _QUALITY_CRITERIA
    + "quality PHẢI copy Y HỆT một trong các chuỗi nhãn ở trên. "
    "Trả về JSON thuần: {\"plate\": \"...\", \"quality\": \"...\", \"confidence\": 0..1}."
)


# Coarse groups so the local signal (which can only judge broadly) and the LLM (which names a
# fine category) are compared fairly: "che vật lý" and "dán decal" both fall under occlusion, so
# they should count as agreement, not a spurious conflict.
_QUALITY_GROUP = {
    "Biển đẹp bình thường": "ok",
    "Biển bẩn": "degrade",
    "Biển cũ, xước, mờ": "degrade",
    "Biển bị che vật lý": "occlusion",
    "Biển bị dán (icon, decal, trang trí...)": "occlusion",
    "Biển giả mạo (che 1 vài số, dán biển khác lên...)": "occlusion",
    "Biển bóng của vật thể che khuất": "occlusion",
    "Biển bị chói sáng": "glare",
    "Biển biến dạng vật lý (cong, vênh)": "deform",
    "Xe không biển": "no_plate",
}


def quality_group(label: str) -> str:
    return _QUALITY_GROUP.get(label, "")


def local_quality_groups(
    classification: str | None, quality_flags: list[str], quality_score: float | None
) -> set[str]:
    """Deterministic SECOND opinion on plate quality from signals the pipeline already has.

    Returns the SET of coarse groups the local evidence is compatible with. The LLM's fine label
    is accepted (agreement) when its group is in this set; otherwise the two disagree and a human
    picks the category.
    """

    if classification == "NO_PLATE":
        return {"no_plate"}
    groups: set[str] = set()
    if "WEAK_CHARACTER" in quality_flags:
        groups |= {"occlusion", "glare"}  # an uncertain character is usually cover or glare
    if quality_score is not None and quality_score < 0.5:
        groups |= {"degrade", "glare"}  # a poor crop is blur / dirt / glare
    return groups or {"ok"}


@dataclass(frozen=True, slots=True)
class CloudReading:
    """Second-opinion read of a plate crop from a cloud vision model."""

    plate: str  # raw text as returned (letters/digits only), may be empty
    quality: str  # one of _QUALITY_CATEGORIES, or "" if the model returned something off-list
    confidence: float
    model: str


def read_plate(
    image_bytes: bytes, *, base_url: str, api_key: str, model: str, timeout: float
) -> CloudReading | None:
    """Read+classify a plate crop via any OpenAI-compatible vision endpoint (GPT, Qwen…).

    Returns None on any network/parsing failure so the caller treats the plate as
    "not cross-checked" instead of blocking.
    """

    if not api_key:
        return None
    encoded = base64.b64encode(_upscale_for_ai(image_bytes)).decode("ascii")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                    },
                ],
            }
        ],
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        url=f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    # The Docker embedded DNS (127.0.0.11) intermittently drops when the cross-check fires many
    # calls back-to-back ("No address associated with hostname"), and the API can return a
    # transient 429/5xx. Retry those a few times — a flake almost always succeeds on the next try.
    parsed = None
    for attempt in range(_MAX_RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            if not content:  # empty/None content (refusal, filter) — skip, don't retry
                return None
            parsed = json.loads(content)
            break
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < _MAX_RETRIES - 1:
                time.sleep(0.8 * (attempt + 1))
                continue
            return None  # auth / not-found / bad-request: retrying won't help
        except (urllib.error.URLError, OSError, TimeoutError):
            if attempt < _MAX_RETRIES - 1:
                time.sleep(0.6 * (attempt + 1))  # transient DNS / connection flake — retry
                continue
            return None
        except (KeyError, IndexError, ValueError, TypeError):
            return None  # unexpected response shape — this reader just doesn't vote
    if parsed is None:
        return None

    plate = "".join(ch for ch in str(parsed.get("plate", "")).upper() if ch.isalnum())
    quality = str(parsed.get("quality", "")).strip()
    if quality not in _QUALITY_CATEGORIES:
        quality = ""
    try:
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return CloudReading(plate=plate, quality=quality, confidence=confidence, model=model)


def read_plate_openai(image_bytes: bytes, settings: Settings) -> CloudReading | None:
    return read_plate(
        image_bytes,
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key if settings.openai_available else "",
        model=settings.openai_ocr_model,
        timeout=settings.cloud_ocr_timeout_s,
    )


def read_plate_qwen(image_bytes: bytes, settings: Settings) -> CloudReading | None:
    return read_plate(
        image_bytes,
        base_url=settings.qwen_base_url,
        api_key=settings.qwen_api_key if settings.qwen_available else "",
        model=settings.qwen_ocr_model,
        timeout=settings.cloud_ocr_timeout_s,
    )
