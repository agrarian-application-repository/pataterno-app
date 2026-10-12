#!/usr/bin/env python3
"""
Farm-side data producer - the ground end of the satellite link.

Testbed 2 (DLR) accepts real data "by integrating a dedicated data producer
server, whose outputs are then forwarded by the IoT devices over the emulated
link". This is that producer. For each drone frame it:

  1. reduces the frame to candidate crops (src/vision.py),
  2. queues three messages by priority -
       P0 summary  (< 1 kB: "candidates at these positions")
       P1 crops    (what the edge classifier needs)
       P2 preview  (small JPEG for the map, only if the link has room)
  3. sends them to the edge application across the link, highest priority
     first, holding them (store-and-forward) while the link is down.

So when a LEO pass is short or the link drops, all pending summaries go before
any crop, and all crops before any preview - the WP4 "priority queuing for
intermittent connectivity".

It records, per frame, the KPI-6 bytes (raw frame vs what was sent) and the
KPI-2 timings (frame in at the producer -> summary received / crops
classified at the edge), on one clock, and writes them to a JSON file. In the
report, `alert_at_edge` is the time the P0 summary reached the edge and
`classified_at_edge` the detection time, which is the one KPI-2 counts (key
names kept so earlier result files stay comparable).

    ROLE=producer EDGE_URL=http://192.168.1.2:80 FRAMES_DIR=/frames python src/app.py
    python src/producer.py --frames DIR --edge URL [--manifest frames.json] [--out results.json]
"""

from __future__ import annotations

import argparse
import heapq
import http.client
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import vision  # noqa: E402

EXTS = {".png", ".jpg", ".jpeg"}
KINDS = {0: ("p0_summary", "/images/summary"), 1: ("p1_crops", "/images/package"), 2: ("p2_preview", "/images/preview")}


def _log(msg: str) -> None:
    print(f"PRODUCER {msg}", flush=True)


# One TCP connection per edge, reused for every message. At a satellite round
# trip of ~0.5 s, opening a connection per message costs a handshake plus TCP
# slow start each time - more than the bandwidth itself (worked trace, M9:
# 101.8 kB of crops took 2.7 s on a 2.6 Mbit/s link). PERSISTENT=0 restores the
# connection-per-message behaviour, for comparison.
_conns: dict[tuple[str, int], http.client.HTTPConnection] = {}
_conn_lock = threading.Lock()
CONNECTS = {"count": 0}


def _persistent() -> bool:
    return os.getenv("PERSISTENT", "1").strip().lower() not in {"0", "false", "no", "off"}


def _decode(status: int, raw: bytes) -> tuple[int, dict]:
    try:
        return status, json.loads(raw.decode("utf-8"))
    except ValueError:
        return status, {}


def _close_conn(key: tuple[str, int]) -> None:
    conn = _conns.pop(key, None)
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _post(url: str, body: bytes, timeout: float) -> tuple[int, dict]:
    """POST a JSON body. Raises OSError / HTTPException when the link is down."""
    if not _persistent():
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        CONNECTS["count"] += 1
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _decode(resp.status, resp.read())

    parts = urllib.parse.urlsplit(url)
    key = (parts.hostname or "localhost", parts.port or 80)
    path = parts.path or "/"
    with _conn_lock:
        # Two tries: a reused connection may have been closed by the server
        # while idle (keep-alive timeout); that is not a link failure, so the
        # message is resent at once on a fresh connection.
        for attempt in (1, 2):
            conn = _conns.get(key)
            fresh = conn is None
            if fresh:
                conn = http.client.HTTPConnection(key[0], key[1], timeout=timeout)
                _conns[key] = conn
                CONNECTS["count"] += 1
            try:
                conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
                resp = conn.getresponse()
                return _decode(resp.status, resp.read())
            except (http.client.RemoteDisconnected, http.client.CannotSendRequest,
                    BrokenPipeError, ConnectionResetError):
                _close_conn(key)
                if fresh or attempt == 2:
                    raise
            except Exception:
                _close_conn(key)
                raise
    raise ConnectionError("unreachable")


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    k = max(0, min(len(values) - 1, int(round(q * (len(values) - 1)))))
    return round(values[k], 3)


class Producer:
    def __init__(self, edge: str, frames: list[Path], manifest: dict, interval: float,
                 repeat: int, send_preview: bool, timeout: float, retry_delay: float,
                 deadline_s: float):
        self.edge = edge.rstrip("/")
        self.frames = frames
        self.manifest = manifest
        self.interval = interval
        self.repeat = max(1, repeat)
        self.send_preview = send_preview
        self.timeout = timeout
        self.retry_delay = retry_delay
        self.deadline = time.time() + deadline_s
        self.queue: list = []
        self.cv = threading.Condition()
        self.done_producing = False
        self.seq = 0
        self.items: list[dict] = []
        self.frame_docs: dict[str, dict] = {}
        self.link_events: list[dict] = []

    # -- production ---------------------------------------------------------

    def produce(self) -> None:
        roi = vision.RoiConfig.from_env()
        for rep in range(self.repeat):
            for path in self.frames:
                key = f"{path.name}#{rep}" if self.repeat > 1 else path.name
                meta_in = dict(self.manifest.get(path.name, {}))
                t_in = time.time()
                meta = {**meta_in, "t_gateway_in": t_in}
                data = path.read_bytes()
                t0 = time.perf_counter()
                pkg = vision.build_package(data, key, meta, roi, with_preview=self.send_preview)
                prep_ms = (time.perf_counter() - t0) * 1000
                kpi6 = pkg.kpi6()
                self.frame_docs[key] = {
                    "frame": key,
                    "meta": meta_in,
                    "t_gateway_in": t_in,
                    "gateway_prep_ms": round(prep_ms, 1),
                    "candidates": len(pkg.candidates),
                    "kpi6": kpi6,
                }
                _log(f"frame {key}: {len(pkg.candidates)} candidates, raw {kpi6['raw_bytes']} B -> "
                     f"sent {kpi6['sent_total_bytes']} B ({prep_ms:.0f} ms)")
                bodies = {0: pkg.summary_doc(), 1: pkg.crops_doc()}
                if self.send_preview and pkg.preview:
                    bodies[2] = pkg.preview_doc()
                with self.cv:
                    for prio, doc in bodies.items():
                        body = json.dumps(doc, separators=(",", ":")).encode("utf-8")
                        item = {"frame": key, "priority": prio, "kind": KINDS[prio][0], "bytes": len(body),
                                "t_enqueued": time.time(), "attempts": 0, "_body": body}
                        heapq.heappush(self.queue, (prio, self.seq, item))
                        self.seq += 1
                    self.cv.notify()
                if self.interval > 0:
                    time.sleep(self.interval)
        with self.cv:
            self.done_producing = True
            self.cv.notify()

    # -- sending ------------------------------------------------------------

    def send(self) -> None:
        link_up = None
        while True:
            with self.cv:
                while not self.queue and not self.done_producing:
                    self.cv.wait(timeout=1.0)
                if not self.queue and self.done_producing:
                    return
                if time.time() > self.deadline:
                    _log(f"deadline reached with {len(self.queue)} items unsent")
                    return
                prio, seq, item = heapq.heappop(self.queue)
            item["attempts"] += 1
            item.setdefault("t_first_try", time.time())
            t_start = time.time()
            try:
                status, resp = _post(self.edge + KINDS[prio][1], item["_body"], self.timeout)
            except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException) as exc:
                if link_up is not False:
                    self.link_events.append({"t": time.time(), "event": "down", "error": type(exc).__name__})
                    _log(f"link down ({type(exc).__name__}); holding {len(self.queue) + 1} items")
                link_up = False
                with self.cv:
                    heapq.heappush(self.queue, (prio, seq, item))
                time.sleep(self.retry_delay)
                continue
            if link_up is False:
                self.link_events.append({"t": time.time(), "event": "up"})
                _log("link up; draining queue by priority")
            link_up = True
            item.update({"t_send_start": t_start, "t_ack": time.time(), "http": status})
            if prio == 1:
                item["edge"] = {k: resp.get(k) for k in ("crops", "classify_ms", "t_classified", "db_written")}
                item["edge"]["detections"] = len(resp.get("detections") or [])
            self.items.append(item)

    # -- run ----------------------------------------------------------------

    def run(self) -> dict:
        t_start = time.time()
        sender = threading.Thread(target=self.send, daemon=True)
        sender.start()
        self.produce()
        sender.join()
        return self.report(t_start)

    def report(self, t_start: float) -> dict:
        frames = []
        for key, doc in self.frame_docs.items():
            its = {i["kind"]: i for i in self.items if i["frame"] == key}
            t_in = doc["t_gateway_in"]
            p0, p1, p2 = its.get("p0_summary"), its.get("p1_crops"), its.get("p2_preview")
            t_cls = (p1 or {}).get("edge", {}).get("t_classified")
            frames.append(
                {
                    **doc,
                    "latency_s": {
                        "alert_at_edge": round(p0["t_ack"] - t_in, 3) if p0 else None,
                        "classified_at_edge": round(t_cls - t_in, 3) if t_cls else None,
                        "verdict_back_at_gateway": round(p1["t_ack"] - t_in, 3) if p1 else None,
                        "preview_delivered": round(p2["t_ack"] - t_in, 3) if p2 else None,
                    },
                    "edge": (p1 or {}).get("edge"),
                    "attempts": {k: v["attempts"] for k, v in its.items()},
                }
            )
        raw = sum(f["kpi6"]["raw_bytes"] for f in frames)
        jpeg = sum(f["kpi6"]["naive_jpeg_bytes"] for f in frames)
        essential = sum(f["kpi6"]["sent_essential_bytes"] for f in frames)
        sent = sum(i["bytes"] for i in self.items)
        alert_lat = [f["latency_s"]["alert_at_edge"] for f in frames if f["latency_s"]["alert_at_edge"] is not None]
        cls_lat = [f["latency_s"]["classified_at_edge"] for f in frames if f["latency_s"]["classified_at_edge"] is not None]
        delivered_order = [f"{i['kind']}:{i['frame']}" for i in sorted(self.items, key=lambda i: i["t_ack"])]
        return {
            "edge": self.edge,
            "started": t_start,
            "duration_s": round(time.time() - t_start, 2),
            "frames": frames,
            "totals": {
                "frames": len(frames),
                "raw_bytes": raw,
                "naive_jpeg_bytes": jpeg,
                "essential_bytes": essential,
                "sent_bytes_delivered": sent,
                "reduction_vs_raw_essential": round(1 - essential / raw, 4) if raw else None,
                "reduction_vs_raw_delivered": round(1 - sent / raw, 4) if raw else None,
                "reduction_vs_jpeg_delivered": round(1 - sent / jpeg, 4) if jpeg else None,
                "unsent_items": len(self.queue),
            },
            "latency_s": {
                "alert_p50": _pct(alert_lat, 0.5), "alert_max": _pct(alert_lat, 1.0),
                "classified_p50": _pct(cls_lat, 0.5), "classified_max": _pct(cls_lat, 1.0),
                "classified_mean": round(statistics.mean(cls_lat), 3) if cls_lat else None,
            },
            "link_events": self.link_events,
            "tcp_connections": CONNECTS["count"],
            "persistent": _persistent(),
            "delivery_order": delivered_order,
            "items": [{k: v for k, v in i.items() if k != "_body"} for i in self.items],
        }


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() not in {"0", "false", "no", "off", ""}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PATATERNO ground gateway data producer")
    ap.add_argument("--edge", default=os.getenv("EDGE_URL", "http://192.168.1.2:80"))
    ap.add_argument("--frames", default=os.getenv("FRAMES_DIR", "/frames"))
    ap.add_argument("--manifest", default=os.getenv("FRAMES_MANIFEST", ""))
    ap.add_argument("--interval", type=float, default=float(os.getenv("FRAME_INTERVAL_S", "0")))
    ap.add_argument("--repeat", type=int, default=int(os.getenv("REPEAT", "1")))
    ap.add_argument("--no-preview", action="store_true", default=not _env_bool("SEND_PREVIEW", True))
    ap.add_argument("--timeout", type=float, default=float(os.getenv("HTTP_TIMEOUT_S", "30")))
    ap.add_argument("--retry-delay", type=float, default=float(os.getenv("RETRY_DELAY_S", "2")))
    ap.add_argument("--deadline", type=float, default=float(os.getenv("RUN_SECONDS", "3600")))
    ap.add_argument("--out", default=os.getenv("RESULTS_PATH", ""))
    args = ap.parse_args(argv)

    frames_dir = Path(args.frames)
    frames = sorted(p for p in frames_dir.iterdir() if p.suffix.lower() in EXTS) if frames_dir.is_dir() else []
    if not frames:
        _log(f"no frames in {frames_dir}")
        return 2
    manifest_path = Path(args.manifest) if args.manifest else frames_dir / "frames.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}

    _log(f"{len(frames)} frames x{args.repeat} -> {args.edge} (preview={'off' if args.no_preview else 'on'})")
    producer = Producer(args.edge, frames, manifest, args.interval, args.repeat, not args.no_preview,
                        args.timeout, args.retry_delay, args.deadline)
    report = producer.run()
    t = report["totals"]
    _log(f"done in {report['duration_s']} s: raw {t['raw_bytes']} B, delivered {t['sent_bytes_delivered']} B "
         f"(-{(t['reduction_vs_raw_delivered'] or 0) * 100:.2f}%), latency {report['latency_s']}")
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
        _log(f"results -> {args.out}")
    else:
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
