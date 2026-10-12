"""
CPB classifier - the KPI-7 INT8 model behind /images.

EfficientNet-B0 fine-tuned on Colorado potato beetle vs. not-CPB, quantised to
INT8 with OpenVINO NNCF (6.12 MB, 96.21% on the 448-image held-out set, KPI-7
report). The IR expects exactly the evaluation transform used in training:
resize the short side to 257 px, centre-crop 224, scale to [0, 1], normalise
with the ImageNet mean/std, NCHW float32. Any other transform silently lowers
accuracy, so it lives here and nowhere else.

OpenVINO is imported lazily: the API must still start (and report the model
as unavailable) on a machine without the runtime.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = _REPO_ROOT / "models" / "cpb_int8" / "model.xml"

# torchvision ImageFolder order at training time: sorted folder names.
CLASSES = ("cpb", "not_cpb")
IMG_SIZE = 224
RESIZE_TO = int(IMG_SIZE * 1.15)  # 257, as in train.py / quantize_nncf.py
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)

# Reported figures of the shipped artefact (KPI-7 report, held-out set).
REFERENCE = {
    "architecture": "EfficientNet-B0 (timm), binary cpb / not_cpb",
    "quantisation": "OpenVINO NNCF post-training INT8 (MIXED preset)",
    "heldout_accuracy": 0.9621,
    "heldout_images": 448,
    "fp32_accuracy": 0.971,
}

_lock = threading.Lock()
_compiled = None
_load_error: str | None = None


def model_path() -> Path:
    return Path(os.getenv("CPB_MODEL", str(DEFAULT_MODEL)))


def preprocess(img: Image.Image) -> np.ndarray:
    """PIL image -> (3, 224, 224) float32, identical to the training eval transform."""
    img = img.convert("RGB")
    w, h = img.size
    # torchvision.transforms.Resize(int): short side -> size, long side
    # truncated with int(), not rounded. Rounding costs one image in 448.
    if w <= h:
        size = (RESIZE_TO, int(RESIZE_TO * h / w))
    else:
        size = (int(RESIZE_TO * w / h), RESIZE_TO)
    img = img.resize(size, Image.BILINEAR)
    w, h = img.size
    # torchvision CenterCrop rounds the offsets.
    left, top = int(round((w - IMG_SIZE) / 2.0)), int(round((h - IMG_SIZE) / 2.0))
    img = img.crop((left, top, left + IMG_SIZE, top + IMG_SIZE))
    x = np.asarray(img, dtype=np.float32).transpose(2, 0, 1) / 255.0
    return (x - _MEAN) / _STD


def _load():
    global _compiled, _load_error
    with _lock:
        if _compiled is not None or _load_error is not None:
            return _compiled
        path = model_path()
        try:
            import openvino as ov  # heavy import, deferred on purpose

            core = ov.Core()
            _compiled = core.compile_model(str(path), "CPU")
        except Exception as exc:  # noqa: BLE001 - report, never crash the API
            _load_error = f"{type(exc).__name__}: {exc}"
        return _compiled


def available() -> bool:
    return _load() is not None


def info() -> dict:
    path = model_path()
    xml_bytes = path.stat().st_size if path.exists() else None
    bin_path = path.with_suffix(".bin")
    bin_bytes = bin_path.stat().st_size if bin_path.exists() else None
    loaded = available()
    runtime = None
    if loaded:
        import openvino as ov

        runtime = ov.get_version()
    return {
        "loaded": loaded,
        "error": _load_error,
        "path": str(path.relative_to(_REPO_ROOT)) if path.is_relative_to(_REPO_ROOT) else str(path),
        "size_mb": round(((xml_bytes or 0) + (bin_bytes or 0)) / 1e6, 2) if bin_bytes else None,
        "classes": list(CLASSES),
        "input": f"1x3x{IMG_SIZE}x{IMG_SIZE} float32, ImageNet-normalised",
        "runtime": runtime,
        **REFERENCE,
    }


def _softmax(logits: np.ndarray) -> np.ndarray:
    z = logits - logits.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def classify(images: list[Image.Image], batch: int = 16) -> list[dict]:
    """Classify crops. -> [{'label', 'p_cpb', 'confidence'}], plus timing on the list.

    Raises RuntimeError if the model could not be loaded.
    """
    compiled = _load()
    if compiled is None:
        raise RuntimeError(f"classifier unavailable: {_load_error}")
    out: list[dict] = []
    for start in range(0, len(images), batch):
        chunk = images[start:start + batch]
        x = np.stack([preprocess(im) for im in chunk])
        logits = compiled(x)[compiled.output(0)]
        probs = _softmax(np.asarray(logits, dtype=np.float32))
        for row in probs:
            idx = int(row.argmax())
            out.append(
                {
                    "label": CLASSES[idx],
                    "p_cpb": round(float(row[0]), 4),
                    "confidence": round(float(row[idx]), 4),
                }
            )
    return out


def timed_classify(images: list[Image.Image]) -> tuple[list[dict], float]:
    t0 = time.perf_counter()
    results = classify(images)
    return results, (time.perf_counter() - t0) * 1000.0
