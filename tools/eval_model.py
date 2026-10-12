#!/usr/bin/env python3
"""Re-measure KPI-1 with the artefact the container actually ships.

    python tools/eval_model.py <dir-with-cpb-and-not_cpb-subfolders> [--json out.json]

The held-out set used for the KPI-7 report is the 448-image `data/val` split
(223 cpb, 225 not_cpb). Running this inside the image proves the deployed
INT8 model, with the container's own preprocessing, reproduces the reported
accuracy - not just the training notebook.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image  # noqa: E402

import classifier  # noqa: E402

EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--json")
    args = ap.parse_args()

    root = Path(args.root)
    items = [
        (p, cls)
        for cls in classifier.CLASSES
        for p in sorted((root / cls).rglob("*"))
        if p.suffix.lower() in EXTS
    ]
    if not items:
        print(f"no images under {root}/{{{','.join(classifier.CLASSES)}}}")
        return 2

    confusion = {t: {p: 0 for p in classifier.CLASSES} for t in classifier.CLASSES}
    t0 = time.perf_counter()
    batch = 32
    for i in range(0, len(items), batch):
        chunk = items[i:i + batch]
        preds = classifier.classify([Image.open(p) for p, _ in chunk])
        for (_, truth), pred in zip(chunk, preds):
            confusion[truth][pred["label"]] += 1
    elapsed = time.perf_counter() - t0

    n = len(items)
    correct = sum(confusion[c][c] for c in classifier.CLASSES)
    tp = confusion["cpb"]["cpb"]
    fp = confusion["not_cpb"]["cpb"]
    fn = confusion["cpb"]["not_cpb"]
    result = {
        "images": n,
        "accuracy": round(correct / n, 4),
        "precision_cpb": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall_cpb": round(tp / (tp + fn), 4) if tp + fn else None,
        "confusion": confusion,
        "ms_per_image_incl_decode": round(elapsed * 1000 / n, 2),
        "model": classifier.info(),
    }
    print(json.dumps(result, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
