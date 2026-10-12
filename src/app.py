#!/usr/bin/env python3
"""
pataterno-app - AGRARIAN Project (Open Call 2, Testbed 2)

Demo application for the PATATERNO project (Precision Agriculture for
poTAto pesT Early Recognition with NOn-terrestrial communications).

It is the PATATERNO edge application of the WP4 architecture: soil-reading
ingestion and queries, the INT8 Colorado-potato-beetle (CPB) classifier behind
/images (whole frames, or the candidate crops a ground gateway sends over the
satellite link), the weather-window job, the farmer dashboard, and `/dbcheck`
for whether the AGRARIAN PostgreSQL is reachable from wherever it runs.

The same image also runs the ground side: ROLE=producer starts the gateway
data producer (src/producer.py) instead of the API.

Developed by Buontech Solutions srl.
"""

import asyncio
import io
import json
import os
import time
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from PIL import Image
from pydantic import BaseModel, Field

import classifier
import dashboard as dashboard_mod
import db
import dbconfig
import vision
import weather
from dashboard import render_dashboard

APP_NAME = "pataterno-app"
APP_VERSION = "1.2.0"


async def _startup_dbcheck() -> None:
    """Report the database verdict at startup, by log AND by database row.

    The log line covers the case where no port is exposed. The row covers the
    case where there is no way to read the log either - which is exactly what
    the Agrarian Portal turned out to be: it shows pod status and offers
    "Delete Pod", but no log viewer and no URL for an app that exposes none.

    So the database doubles as the out-of-band channel: if a row appears in
    `container_pings` carrying this pod's hostname and egress IP, the container
    reached the database from wherever the cluster scheduled it. If no row
    appears while the portal reports the pod Running, it did not.

    Runs in a worker thread and is never awaited by startup: the container must
    become ready immediately, and a slow probe must not race the HEALTHCHECK.
    """
    try:
        doc = await asyncio.to_thread(db.run_dbcheck)
        # default=str because Decimal and datetime are not JSON-serialisable.
        payload = json.dumps(doc, separators=(",", ":"), default=str)
        print("DBCHECK " + payload, flush=True)
        # Third out-of-band channel, for the case the portal gives us neither a
        # log viewer nor a credential to write container_pings: send the very
        # same (credential-free, secret-redacted) diagnostic to a listener we
        # control. See _phone_home.
        await asyncio.to_thread(_phone_home, payload)

        # When a working credential is present, also write the proof row. On the
        # credential-free testbed this is skipped and phone-home is the channel.
        if os.getenv("DBCHECK_WRITE_ON_STARTUP", "1") != "0" and doc.get("auth", {}).get("ok"):
            try:
                note = (
                    f"startup {doc.get('verdict')} · node-side egress "
                    f"{doc['container'].get('egress_ip')}"
                )
                row = await asyncio.to_thread(db.write_ping, note)
                print("DBPING " + json.dumps(row, separators=(",", ":"), default=str), flush=True)
            except BaseException as exc:  # noqa: BLE001
                print("DBPING " + json.dumps({"error": dbconfig.redact(exc)}), flush=True)
    except BaseException as exc:  # noqa: BLE001 - diagnostics never break startup
        print("DBCHECK " + json.dumps({"error": dbconfig.redact(exc)}), flush=True)


def _phone_home(payload: str) -> None:
    """POST the startup diagnostic to a listener we control. Never raises.

    This carries no credential: the diagnostic redacts secrets, and in the
    testbed's credential-free mode it is a pure TCP-reachability result
    (hostname, egress IP, whether the database answered). It is how we read the
    verdict from a machine we cannot log into - nothing more than the container
    reporting its own connectivity to its owner.

    Enabled only when PHONE_HOME_URL is set. The token is a shared secret so the
    listener can ignore unrelated internet noise on its open port; it is not a
    credential for anything.
    """
    url = os.getenv("PHONE_HOME_URL", "").strip()
    if not url:
        return
    token = os.getenv("PHONE_HOME_TOKEN", "").strip()
    attempts = int(os.getenv("PHONE_HOME_ATTEMPTS", "5") or "5")
    delay = float(os.getenv("PHONE_HOME_RETRY_DELAY", "3") or "3")

    data = payload.encode("utf-8")
    for attempt in range(1, max(1, attempts) + 1):
        request = urllib.request.Request(url, data=data, method="POST")
        request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("X-Token", token)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                print(f"PHONEHOME sent (attempt {attempt}, http {response.status})", flush=True)
                return
        except Exception as exc:  # noqa: BLE001 - a beacon must never break startup
            print(f"PHONEHOME attempt {attempt} failed: {dbconfig.redact(exc)}", flush=True)
            if attempt < attempts and delay > 0:
                time.sleep(delay)
    print("PHONEHOME giving up after all attempts", flush=True)


async def _keepalive_loop() -> None:
    """Keep the database path warm so user requests never pay the wake-up cost."""
    interval = dbconfig.get_config().keepalive_s
    if interval <= 0:
        return
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(db.keepalive)
        except asyncio.CancelledError:
            raise
        except BaseException:  # noqa: BLE001 - a heartbeat never breaks the app
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = []
    # Compile the INT8 model in the background, so the first frame does not
    # pay for it and /health is never delayed.
    tasks.append(asyncio.get_running_loop().create_task(asyncio.to_thread(classifier.available)))
    if os.getenv("DBCHECK_ON_STARTUP", "1") != "0":
        loop = asyncio.get_running_loop()
        tasks.append(loop.create_task(_startup_dbcheck()))
        tasks.append(loop.create_task(_keepalive_loop()))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        db.close()


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)

# ---------------------------------------------------------------------------
# In-memory store (demo only — production uses AGRARIAN PostgreSQL)
# ---------------------------------------------------------------------------

READINGS: list[dict] = []

# Mock detections mirroring the 17 June 2026 drone flight over Petrizzo
DETECTIONS: list[dict] = [
    {
        "frame": "DJI_0142.JPG",
        "label": "colorado_potato_beetle",
        "confidence": 0.97,
        "lat": 40.33,
        "lon": 15.61,
        "captured_at": "2026-06-17T10:41:00Z",
    },
    {
        "frame": "DJI_0187.JPG",
        "label": "colorado_potato_beetle",
        "confidence": 0.93,
        "lat": 40.33,
        "lon": 15.61,
        "captured_at": "2026-06-17T10:52:00Z",
    },
]


class SoilReading(BaseModel):
    """One reading from a 7-in-1 RS485 soil probe on a LoRa station."""

    station_id: str = Field(..., pattern=r"^station-[0-9]{2}$", examples=["station-01"])
    moisture_pct: float = Field(..., ge=0, le=100)
    temperature_c: float = Field(..., ge=-40, le=80)
    ec_us_cm: float = Field(..., ge=0)
    ph: float = Field(..., ge=0, le=14)
    nitrogen_mg_kg: Optional[float] = Field(None, ge=0)
    phosphorus_mg_kg: Optional[float] = Field(None, ge=0)
    potassium_mg_kg: Optional[float] = Field(None, ge=0)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/")
async def home():
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "status": "running",
        "description": (
            "PATATERNO demo application for the AGRARIAN portal - "
            "soil telemetry and CPB detection API (Testbed 2)"
        ),
        "project": "PATATERNO - AGRARIAN Open Call 2",
        "organization": "Buontech Solutions srl",
        "image_tag": os.getenv("IMAGE_TAG", "unknown"),
        "db": {
            "mode": dbconfig.get_config().mode,
            "schema": dbconfig.get_config().schema,
        },
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/readings", status_code=201)
async def ingest_reading(reading: SoilReading):
    """Ingest one soil reading (gateway → API path of the WP4 architecture)."""
    row = reading.model_dump()
    row["received_at"] = datetime.now(timezone.utc).isoformat()
    READINGS.append(row)
    return {"stored": True, "count": len(READINGS)}


def _db_first(fetch, fallback):
    """Try the database once; serve memory on any failure. -> (rows, source).

    The circuit breaker inside db.db_available() means a database that is down
    costs one timeout, not one per request.
    """
    if not db.db_available():
        # Say why: silently serving mock data is very hard to debug on the
        # portal, where the pod log is the only thing you can see.
        cfg = dbconfig.get_config()
        reason = "cooling down after a failure" if db.cooling_down() else f"mode={cfg.mode}"
        print(f"DBFALLBACK serving memory ({reason})", flush=True)
        return fallback(), "memory"
    try:
        return fetch(), "db"
    except Exception as exc:  # noqa: BLE001 - degradation is the point
        print(f"DBFALLBACK serving memory: {dbconfig.redact(exc)}", flush=True)
        return fallback(), "memory"


def _provenance(source: str, rows: list[dict]) -> dict:
    """Where the data came from, and whether it is synthetic.

    Reported on every response so nothing downstream - including a screenshot
    of the dashboard - can silently pass sandbox data off as field data.
    """
    cfg = dbconfig.get_config()
    synthetic = source == "db" and (
        cfg.schema != "public"
        or any(row.get("data_source") == "synthetic" for row in rows)
    )
    return {"source": source, "schema": cfg.schema, "synthetic": synthetic}


def _memory_latest() -> list[dict]:
    latest: dict[str, dict] = {}
    for row in READINGS:
        latest[row["station_id"]] = row
    return list(latest.values())


def _memory_stations() -> list[dict]:
    """The mock grid in the database's row shape, each row labelled as mock."""
    return [
        {
            "station_id": station["id"],
            "kind": "soil_station",
            "label": station["id"],
            "lat": None,
            "lon": None,
            "geometry": None,
            "data_source": "mock",
        }
        for station in dashboard_mod.STATIONS
    ]


@app.get("/readings/latest")
def latest_readings():
    """Latest reading per station — from the database when it is reachable."""
    rows, source = _db_first(db.fetch_latest_readings, _memory_latest)
    return {"stations": len(rows), "readings": rows, **_provenance(source, rows)}


@app.get("/stations")
def stations(kind: Optional[str] = None):
    """Station inventory with geometry. `kind=soil_station` drops gateways."""
    rows, source = _db_first(lambda: db.fetch_stations(kind), _memory_stations)
    return {"count": len(rows), "stations": rows, **_provenance(source, rows)}


@app.get("/detections")
def detections(min_confidence: float = 0.0):
    """CPB detections from the classifier."""
    # Validate before touching the database, so a bad request is always 422.
    if not 0.0 <= min_confidence <= 1.0:
        raise HTTPException(status_code=422, detail="min_confidence must be in [0, 1]")

    def _memory() -> list[dict]:
        return [d for d in _memory_detections() if d["confidence"] >= min_confidence]

    rows, source = _db_first(lambda: db.fetch_detections(min_confidence), _memory)
    return {"count": len(rows), "detections": rows, **_provenance(source, rows)}


def _weather_for_dashboard() -> dict | None:
    """The live treatment window, or None (the card then shows its mock text).

    Opt-out with DASHBOARD_WEATHER=0, e.g. in tests: it is a network call.
    """
    if os.getenv("DASHBOARD_WEATHER", "1") == "0":
        return None
    doc = weather.get_window()
    return doc if doc.get("available") else None


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    """Read-only farmer dashboard — real data when available, mock otherwise."""
    window = _weather_for_dashboard()
    bundle = db.dashboard_bundle() if db.db_available() else None
    if bundle is None:
        # All-or-nothing for the field data: a half-real page cannot be
        # captioned honestly. Classifier detections made by this container are
        # real outputs, so they replace the mock list when there are any.
        return render_dashboard(_memory_detections(), meta={"window": window})
    return render_dashboard(
        bundle["detections"],
        stations=dashboard_mod._rows_from_readings(bundle["readings"]),
        series=bundle["series"],
        meta={
            "source": "db",
            "synthetic": bundle["synthetic"],
            "as_of": bundle["as_of"],
            "expected_stations": len(bundle["stations"]) or 9,
            "window": window,
        },
    )


# ---------------------------------------------------------------------------
# Images: the INT8 classifier at the edge
#
# Two ways in. POST /images takes a whole frame and does everything here (the
# portal / single-node case). The /images/summary, /images/package and
# /images/preview trio is what a ground gateway sends over the satellite link
# after reducing the frame itself (src/vision.py) - P0, P1 and P2 of its
# priority queue. KPI-2 timing fields ride along so the producer can measure
# capture-to-alert latency on one clock.
# ---------------------------------------------------------------------------

CLASSIFIED: list[dict] = []   # detections produced by the classifier
ALERTS: list[dict] = []       # one per frame: candidate summary, then verdict
_MAX_KEEP = 500


def _memory_detections() -> list[dict]:
    return list(reversed(CLASSIFIED)) if CLASSIFIED else DETECTIONS


def _keep(store: list, item: dict) -> None:
    store.append(item)
    del store[:-_MAX_KEEP]


def _alert_for(frame: str) -> dict | None:
    for alert in reversed(ALERTS):
        if alert["frame"] == frame:
            return alert
    return None


def _cpb_threshold() -> float:
    try:
        return float(os.getenv("CPB_THRESHOLD", "0.5"))
    except ValueError:
        return 0.5


def _classify_and_record(frame: str, crops, boxes, meta: dict, t_received: float) -> dict:
    """Classify crops, record detections + the frame alert. Runs in a thread."""
    if not crops:
        verdicts, ms = [], 0.0
    else:
        verdicts, ms = classifier.timed_classify(crops)
    threshold = _cpb_threshold()
    now_iso = db.utc_now_z()
    captured_at = meta.get("captured_at") or now_iso
    hits = []
    for box, verdict in zip(boxes, verdicts):
        if verdict["p_cpb"] >= threshold:
            hits.append(
                {
                    "frame": frame,
                    "label": "colorado_potato_beetle",
                    "confidence": verdict["p_cpb"],
                    "box": list(box),
                    "lat": meta.get("lat"),
                    "lon": meta.get("lon"),
                    "captured_at": captured_at,
                }
            )
    for hit in hits:
        _keep(CLASSIFIED, hit)

    written: int | str = 0
    if hits and meta.get("lat") is not None and db.db_available():
        try:
            ids = db.write_detections(
                meta.get("flight_id") or f"edge-{captured_at[:10]}",
                [{**h, "image_ref": f"{frame}#{','.join(map(str, h['box']))}"} for h in hits],
                data_source=os.getenv("DETECTIONS_DATA_SOURCE", "classifier"),
            )
            written = len(ids)
        except Exception as exc:  # noqa: BLE001 - memory still has them
            written = f"not written: {dbconfig.redact(exc)}"

    alert = _alert_for(frame)
    if alert is None:
        alert = {"frame": frame, **{k: meta.get(k) for k in ("flight_id", "captured_at", "lat", "lon", "t_gateway_in")}}
        _keep(ALERTS, alert)
    alert.update(
        {
            "state": "classified",
            "crops": len(verdicts),
            "cpb": len(hits),
            "max_p_cpb": max((v["p_cpb"] for v in verdicts), default=None),
            "classify_ms": round(ms, 1),
            "t_package_received": t_received,
            "t_classified": time.time(),
            "db_written": written,
        }
    )
    return {
        "frame": frame,
        "crops": len(verdicts),
        "detections": hits,
        "verdicts": [{"box": list(b), **v} for b, v in zip(boxes, verdicts)],
        "classify_ms": round(ms, 1),
        "threshold": threshold,
        "db_written": written,
        "t_classified": alert["t_classified"],
    }


class FrameMeta(BaseModel):
    frame: str = Field(..., max_length=200)
    flight_id: Optional[str] = Field(None, max_length=80)
    captured_at: Optional[str] = None
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lon: Optional[float] = Field(None, ge=-180, le=180)
    t_gateway_in: Optional[float] = None


class SummaryIn(FrameMeta):
    n_candidates: int = Field(..., ge=0)
    candidates: list[list[float]] = Field(default_factory=list)


class CropIn(BaseModel):
    box: list[int] = Field(..., min_length=4, max_length=4)
    jpeg_b64: str


class PackageIn(FrameMeta):
    crops: list[CropIn] = Field(default_factory=list, max_length=64)


class PreviewIn(FrameMeta):
    jpeg_b64: str


@app.get("/model")
def model_info():
    """The shipped classifier: artefact, size, reference accuracy, runtime."""
    return {**classifier.info(), "roi": vision.config_dict(vision.RoiConfig.from_env()),
            "threshold": _cpb_threshold()}


@app.post("/images")
async def images(request: Request, frame: str = "upload", lat: Optional[float] = None,
                 lon: Optional[float] = None, captured_at: Optional[str] = None,
                 flight_id: Optional[str] = None):
    """Whole frame in (raw image bytes as the body), detections out.

    The container does the gateway's reduction itself here, and reports the
    KPI-6 figures for the frame alongside the verdicts.
    """
    body = await request.body()
    if not body:
        raise HTTPException(status_code=422, detail="send the image bytes as the request body")
    t_received = time.time()
    meta = {"lat": lat, "lon": lon, "captured_at": captured_at, "flight_id": flight_id,
            "t_gateway_in": t_received}

    def work() -> dict:
        try:
            package = vision.build_package(body, frame, meta, vision.RoiConfig.from_env())
        except Exception as exc:  # noqa: BLE001 - PIL's errors are the client's fault
            raise HTTPException(status_code=422, detail=f"not a readable image: {type(exc).__name__}") from None
        crops = [Image.open(io.BytesIO(c)).convert("RGB") for c in package.crops]
        result = _classify_and_record(frame, crops, package.boxes, meta, t_received)
        return {**result, "kpi6": package.kpi6(), "candidates": len(package.candidates)}

    if not classifier.available():
        return JSONResponse(status_code=503, content={"error": "classifier unavailable", "model": classifier.info()})
    return await asyncio.to_thread(work)


@app.post("/images/summary", status_code=202)
def images_summary(body: SummaryIn):
    """P0: a sub-kilobyte alert that candidates exist - before any crop arrives."""
    alert = _alert_for(body.frame)
    if alert is None:
        alert = {"frame": body.frame}
        _keep(ALERTS, alert)
    alert.update(
        {
            **body.model_dump(exclude={"candidates"}),
            "state": alert.get("state", "candidates"),
            "t_summary_received": time.time(),
        }
    )
    return {"accepted": True, "frame": body.frame, "t_received": alert["t_summary_received"]}


@app.post("/images/package")
def images_package(body: PackageIn):
    """P1: the candidate crops. Classified here; detections recorded."""
    if not classifier.available():
        return JSONResponse(status_code=503, content={"error": "classifier unavailable"})
    t_received = time.time()
    try:
        crops = [vision.decode_crop(c.jpeg_b64) for c in body.crops]
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=422, detail="a crop is not a readable JPEG") from None
    meta = body.model_dump(exclude={"crops"})
    return _classify_and_record(body.frame, crops, [tuple(c.box) for c in body.crops], meta, t_received)


@app.post("/images/preview", status_code=202)
def images_preview(body: PreviewIn):
    """P2: low-resolution frame preview for the map. Stored as size + time only."""
    alert = _alert_for(body.frame)
    if alert is not None:
        alert["preview_bytes"] = len(body.jpeg_b64) * 3 // 4
        alert["t_preview_received"] = time.time()
    return {"accepted": True, "frame": body.frame}


@app.get("/alerts")
def alerts(limit: int = 50):
    """Per-frame alerts with their timing fields (KPI-2 instrumentation)."""
    limit = max(1, min(limit, _MAX_KEEP))
    return {"count": len(ALERTS), "alerts": list(reversed(ALERTS[-limit:]))}


@app.get("/weather/window")
def weather_window(lat: Optional[float] = None, lon: Optional[float] = None, refresh: bool = False,
                   hourly: bool = False):
    """Rain-free treatment windows over the next 96 h (Open-Meteo forecast)."""
    doc = weather.get_window(lat, lon, refresh=refresh)
    if not hourly:
        doc = {k: v for k, v in doc.items() if k != "hourly"}
    return doc


# ---------------------------------------------------------------------------
# Database diagnostics
#
# Both handlers are plain `def`, so FastAPI runs them in a worker thread: a
# blocking connect inside an `async def` would stall the event loop — and with
# it /health, whose Docker HEALTHCHECK times out after 3 s.
# ---------------------------------------------------------------------------


class PingNote(BaseModel):
    note: Optional[str] = Field(None, max_length=200)


@app.get("/dbcheck")
def dbcheck(
    stages: Literal["all", "tcp", "auth"] = "all",
    force: bool = False,
    reload: bool = False,
):
    """Is the AGRARIAN database reachable from here, and if not, why not?

    Always answers 200: a diagnostic that returns 500 tells you nothing.
    """
    if reload:
        dbconfig.reload_config()
    return db.run_dbcheck(stages=stages, force=force)


@app.post("/dbcheck/write", status_code=201)
def dbcheck_write(body: Optional[PingNote] = None):
    """Write one marker row into <schema>.container_pings — the proof row."""
    note = body.note if body is not None else None
    try:
        row = db.write_ping(note)
    except PermissionError as exc:
        return JSONResponse(
            status_code=409,
            content={"written": False, "stage": "guard", "error": str(exc)},
        )
    except db.DbUnavailable as exc:
        return JSONResponse(
            status_code=503,
            content={"written": False, "stage": "write", "error": str(exc)},
        )
    return {"written": True, **row, "target": dbconfig.get_config().public_dict()}


if __name__ == "__main__":
    if os.getenv("ROLE", "edge").strip().lower() == "producer":
        # Same image, ground side: the gateway data producer (Testbed 2's
        # "data producer server" feeding the emulated IoT node).
        import producer

        raise SystemExit(producer.main())

    import uvicorn

    # Port 80 by default so the Dockerfile and its HEALTHCHECK keep working;
    # override locally, where binding 80 needs privileges.
    uvicorn.run(
        app,
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", os.getenv("PORT", "80"))),
    )
