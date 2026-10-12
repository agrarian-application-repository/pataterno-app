"""
Ground-side image preprocessing - what crosses the satellite link instead of
the frame.

A drone frame is reduced to the few regions that could contain a Colorado
potato beetle, and only those crops (plus a sub-kilobyte summary and an
optional low-resolution preview) are sent. This is the WP2 "preprocessing"
step and the source of the KPI-6 bandwidth reduction.

Candidate regions: CPB adults (hue ~28 deg) and larvae (~15-30 deg) are
saturated orange-brown; dry soil sits at ~37-38 deg and is less saturated;
potato flowers are yellow-white. A pixel is a candidate when its hue is in
[5, 32] deg (or >= 350), saturation >= 0.55 and value >= 0.35. Because the
beetle feeds on foliage, a candidate tile must also have green leaf around
it - that single rule removes the bare-soil clods the classifier (trained on
close-ups whose negatives never include soil) would otherwise accept.
The thresholds were set on the 17 June frames and are configurable.

Pure numpy + Pillow: it must run on the Raspberry Pi gateway as well as in
the container.
"""

from __future__ import annotations

import base64
import io
import json
import os
from dataclasses import asdict, dataclass, field

import numpy as np
from PIL import Image


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass
class RoiConfig:
    downscale: int = 4          # analysis resolution = frame / downscale
    tile: int = 24              # tile side at analysis resolution (96 px full-res)
    stride: int = 12
    hue_max_deg: float = 32.0
    hue_min_deg: float = 5.0
    sat_min: float = 0.55
    val_min: float = 0.35
    frac_min: float = 0.004     # beetle pixels in a tile: a speck ...
    frac_max: float = 0.12      # ... but not a patch of soil
    green_min: float = 0.20     # leaf context around the tile
    ignore_bottom: float = 0.10 # DJI on-screen-display band
    max_candidates: int = 24
    min_separation_px: int = 96
    crop_px: int = 224          # crop side at full resolution
    crop_quality: int = 85
    preview_px: int = 640       # long side of the optional preview
    preview_quality: int = 60

    @classmethod
    def from_env(cls) -> "RoiConfig":
        cfg = cls()
        for name in cfg.__dataclass_fields__:
            env = "ROI_" + name.upper()
            if env in os.environ:
                current = getattr(cfg, name)
                value = _env_float(env, current)
                setattr(cfg, name, int(value) if isinstance(current, int) else value)
        return cfg


@dataclass
class Candidate:
    cx: float
    cy: float
    score: float


def find_candidates(img: Image.Image, cfg: RoiConfig | None = None) -> list[Candidate]:
    """Return candidate beetle locations in full-resolution pixel coordinates."""
    cfg = cfg or RoiConfig()
    img = img.convert("RGB")
    w, h = img.size
    ds = max(1, cfg.downscale)
    small = img.resize((max(1, w // ds), max(1, h // ds)), Image.BILINEAR)
    hsv = np.asarray(small.convert("HSV"), dtype=np.float32)
    hue = hsv[..., 0] * (360.0 / 255.0)
    sat = hsv[..., 1] / 255.0
    val = hsv[..., 2] / 255.0

    hue_ok = ((hue >= cfg.hue_min_deg) & (hue <= cfg.hue_max_deg)) | (hue >= 350.0)
    mask = hue_ok & (sat >= cfg.sat_min) & (val >= cfg.val_min)
    green = (hue >= 60.0) & (hue <= 160.0) & (sat >= 0.25) & (val >= 0.20)
    if cfg.ignore_bottom > 0:
        mask[int(mask.shape[0] * (1.0 - cfg.ignore_bottom)):, :] = False

    t, s = cfg.tile, cfg.stride
    found: list[Candidate] = []
    rows, cols = mask.shape
    for y in range(0, max(1, rows - t + 1), s):
        for x in range(0, max(1, cols - t + 1), s):
            tile = mask[y:y + t, x:x + t]
            frac = float(tile.mean()) if tile.size else 0.0
            if not cfg.frac_min <= frac <= cfg.frac_max:
                continue
            ctx = green[max(0, y - t):y + 2 * t, max(0, x - t):x + 2 * t]
            if ctx.size == 0 or float(ctx.mean()) < cfg.green_min:
                continue
            ys, xs = np.nonzero(tile)
            found.append(Candidate((x + xs.mean()) * ds, (y + ys.mean()) * ds, frac))

    found.sort(key=lambda c: c.score, reverse=True)
    picked: list[Candidate] = []
    sep2 = cfg.min_separation_px ** 2
    for cand in found:
        if all((cand.cx - p.cx) ** 2 + (cand.cy - p.cy) ** 2 > sep2 for p in picked):
            picked.append(cand)
        if len(picked) >= cfg.max_candidates:
            break
    return picked


def crop_box(img_size: tuple[int, int], cx: float, cy: float, side: int) -> tuple[int, int, int, int]:
    w, h = img_size
    side = min(side, w, h)
    left = int(max(0, min(w - side, cx - side / 2)))
    top = int(max(0, min(h - side, cy - side / 2)))
    return left, top, left + side, top + side


def _jpeg(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


@dataclass
class Package:
    """Everything the gateway would send for one frame, by priority.

    P0 summary  - sub-kilobyte alert: where candidates are. Sent first, survives
                  even a very short satellite pass.
    P1 crops    - the candidate crops the edge classifier needs (KPI-6 payload).
    P2 preview  - a small JPEG of the whole frame for the farmer's map; sent
                  only when the link has room.
    """

    frame: str
    meta: dict
    candidates: list[Candidate]
    crops: list[bytes]
    preview: bytes | None
    raw_bytes: int
    naive_jpeg_bytes: int
    boxes: list[tuple[int, int, int, int]] = field(default_factory=list)

    def summary_doc(self) -> dict:
        return {
            "frame": self.frame,
            **self.meta,
            "n_candidates": len(self.candidates),
            "candidates": [[round(c.cx), round(c.cy)] for c in self.candidates],
        }

    def crops_doc(self) -> dict:
        return {
            "frame": self.frame,
            **self.meta,
            "crops": [
                {"box": list(box), "jpeg_b64": base64.b64encode(data).decode("ascii")}
                for box, data in zip(self.boxes, self.crops)
            ],
        }

    def preview_doc(self) -> dict:
        return {
            "frame": self.frame,
            **self.meta,
            "jpeg_b64": base64.b64encode(self.preview or b"").decode("ascii"),
        }

    def wire_sizes(self) -> dict:
        """Bytes of each HTTP body as actually sent (JSON, base64 included)."""
        enc = lambda d: len(json.dumps(d, separators=(",", ":")).encode("utf-8"))  # noqa: E731
        return {
            "p0_summary": enc(self.summary_doc()),
            "p1_crops": enc(self.crops_doc()) if self.crops else 0,
            "p2_preview": enc(self.preview_doc()) if self.preview else 0,
        }

    def kpi6(self) -> dict:
        sizes = self.wire_sizes()
        essential = sizes["p0_summary"] + sizes["p1_crops"]
        total = essential + sizes["p2_preview"]
        return {
            "raw_bytes": self.raw_bytes,
            "naive_jpeg_bytes": self.naive_jpeg_bytes,
            **sizes,
            "sent_essential_bytes": essential,
            "sent_total_bytes": total,
            "reduction_vs_raw_essential": round(1 - essential / self.raw_bytes, 4) if self.raw_bytes else None,
            "reduction_vs_raw_total": round(1 - total / self.raw_bytes, 4) if self.raw_bytes else None,
            "reduction_vs_jpeg_total": round(1 - total / self.naive_jpeg_bytes, 4) if self.naive_jpeg_bytes else None,
        }


def build_package(
    frame_bytes: bytes,
    frame: str,
    meta: dict | None = None,
    cfg: RoiConfig | None = None,
    with_preview: bool = True,
) -> Package:
    cfg = cfg or RoiConfig()
    img = Image.open(io.BytesIO(frame_bytes)).convert("RGB")
    cands = find_candidates(img, cfg)
    boxes = [crop_box(img.size, c.cx, c.cy, cfg.crop_px) for c in cands]
    crops = [_jpeg(img.crop(b), cfg.crop_quality) for b in boxes]
    preview = None
    if with_preview:
        p = img.copy()
        p.thumbnail((cfg.preview_px, cfg.preview_px))
        preview = _jpeg(p, cfg.preview_quality)
    return Package(
        frame=frame,
        meta=dict(meta or {}),
        candidates=cands,
        crops=crops,
        preview=preview,
        raw_bytes=len(frame_bytes),
        naive_jpeg_bytes=len(_jpeg(img, 85)),
        boxes=boxes,
    )


def decode_crop(jpeg_b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(jpeg_b64))).convert("RGB")


def config_dict(cfg: RoiConfig | None = None) -> dict:
    return asdict(cfg or RoiConfig())
