"""Producer priority queue and store-and-forward, with the link faked."""

import io
import json
import urllib.error

import numpy as np
from PIL import Image

import producer


def _frames(tmp_path, n=3):
    for i in range(n):
        arr = np.zeros((540, 960, 3), dtype=np.uint8)
        arr[...] = (60, 140, 50)
        arr[200:216, 400 + 40 * i:418 + 40 * i] = (196, 120, 50)
        Image.fromarray(arr).save(tmp_path / f"f{i}.png")
    (tmp_path / "frames.json").write_text(json.dumps({"f0.png": {"lat": 40.33, "lon": 15.61}}))
    return sorted(tmp_path.glob("*.png"))


def _fake_link(monkeypatch, down_calls: int):
    """Fail the first `down_calls` POSTs (link down), then accept everything."""
    state = {"calls": 0, "delivered": []}

    def fake_post(url, body, timeout):
        state["calls"] += 1
        if state["calls"] <= down_calls:
            raise urllib.error.URLError("no route")
        doc = json.loads(body)
        state["delivered"].append((url.rsplit("/", 1)[-1], doc["frame"]))
        if url.endswith("/images/package"):
            return 200, {"crops": len(doc["crops"]), "classify_ms": 1.0, "t_classified": None,
                         "detections": [], "db_written": 0}
        return 202, {"accepted": True}

    monkeypatch.setattr(producer, "_post", fake_post)
    return state


def test_all_alerts_before_any_crop_after_outage(monkeypatch, tmp_path):
    frames = _frames(tmp_path)
    state = _fake_link(monkeypatch, down_calls=1)
    p = producer.Producer("http://edge", frames, {}, interval=0, repeat=1, send_preview=True,
                          timeout=1, retry_delay=0.2, deadline_s=30)
    report = p.run()
    kinds = [k for k, _ in state["delivered"]]
    # once the link is back, the queue drains strictly by priority
    first_crop = kinds.index("package")
    last_summary = len(kinds) - 1 - kinds[::-1].index("summary")
    first_preview = kinds.index("preview")
    assert last_summary < first_crop < first_preview
    assert report["totals"]["unsent_items"] == 0
    assert [e["event"] for e in report["link_events"]] == ["down", "up"]


def test_report_accounting(monkeypatch, tmp_path):
    frames = _frames(tmp_path, n=2)
    _fake_link(monkeypatch, down_calls=0)
    p = producer.Producer("http://edge", frames, {}, 0, 1, False, 1, 0.1, 30)
    report = p.run()
    t = report["totals"]
    assert t["frames"] == 2
    assert t["sent_bytes_delivered"] == t["essential_bytes"]  # no previews
    assert 0 < t["reduction_vs_raw_delivered"] < 1
    for f in report["frames"]:
        assert f["latency_s"]["alert_at_edge"] is not None
        assert f["candidates"] == 1


def test_main_without_frames(tmp_path):
    assert producer.main(["--frames", str(tmp_path / "missing")]) == 2
