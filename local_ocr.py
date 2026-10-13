"""Optional, offline Chinese/English OCR with locally installed ONNX models."""
from __future__ import annotations

import importlib.util
import io
import threading
import time

_engine = None
_lock = threading.Lock()
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_SIDE = 4096


class OCRError(ValueError):
    pass


def status() -> dict:
    available = all(importlib.util.find_spec(m) is not None for m in ("rapidocr_onnxruntime", "onnxruntime", "PIL"))
    return {"available": available, "engine": "RapidOCR / ONNX Runtime", "device": "CPU",
            "languages": ["中文", "English"], "offline": True,
            "pdf_scan_available": available and importlib.util.find_spec("fitz") is not None,
            "note": "本机离线识别；不占用独立显卡显存" if available else "需安装可选依赖 rapidocr_onnxruntime"}


def recognize(data: bytes) -> dict:
    global _engine
    started = time.monotonic()
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise OCRError("本地 OCR 不可用；请安装 rapidocr_onnxruntime 和 Pillow") from exc
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise OCRError("图片超过 2000 万像素，请裁剪或分成多页")
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
            pixels = np.array(image)[:, :, ::-1].copy()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise OCRError("图片无效或尺寸过大") from exc
    # ponytail: one shared engine and serialized inference bound CPU/memory use.
    with _lock:
        try:
            if _engine is None:
                _engine = RapidOCR()  # Bundled v1 models and config default to CPU.
            rows, _ = _engine(pixels)
        except Exception as exc:
            raise OCRError("本地 OCR 模型加载或识别失败") from exc
    lines = [{"text": str(r[1]), "confidence": round(float(r[2]), 4)} for r in (rows or [])]
    if not lines:
        raise OCRError("图片未识别出文字；请使用更清晰的图片")
    return {"text": "\n".join(r["text"] for r in lines), "method": "local_ocr", "engine": "RapidOCR",
            "device": "CPU", "lines": lines, "duration_ms": round((time.monotonic() - started) * 1000),
            "mean_confidence": round(sum(r["confidence"] for r in lines) / len(lines), 4)}
