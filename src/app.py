#!/usr/bin/env python3
"""
pataterno-app - AGRARIAN Project (Open Call 2, Testbed 2)

Demo application for the PATATERNO project (Precision Agriculture for
poTAto pesT Early Recognition with NOn-terrestrial communications).

It exposes a minimal version of the PATATERNO container API described in
the WP4 architecture: soil-reading ingestion, latest-values query and a
Colorado-potato-beetle (CPB) detection summary. Readings and detections are
still kept in memory; `/dbcheck` reports whether the AGRARIAN PostgreSQL is
reachable from wherever this container runs.

Developed by Buontech Solutions srl.
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

import db
import dbconfig
from dashboard import render_dashboard

APP_NAME = "pataterno-app"
APP_VERSION = "1.1.0"


async def _startup_dbcheck() -> None:
    """Print one DBCHECK line so the verdict survives having no exposed port.

    Runs in a worker thread and is never awaited by startup: the container must
    become ready immediately, and a slow probe must not race the HEALTHCHECK.
    """
    try:
        doc = await asyncio.to_thread(db.run_dbcheck)
        # default=str because Decimal and datetime are not JSON-serialisable.
        print("DBCHECK " + json.dumps(doc, separators=(",", ":"), default=str), flush=True)
    except BaseException as exc:  # noqa: BLE001 - diagnostics never break startup
        print("DBCHECK " + json.dumps({"error": dbconfig.redact(exc)}), flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("DBCHECK_ON_STARTUP", "1") != "0":
        asyncio.get_running_loop().create_task(_startup_dbcheck())
    try:
        yield
    finally:
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


@app.get("/readings/latest")
async def latest_readings():
    """Latest reading per station, like the read-only dashboard view."""
    latest: dict[str, dict] = {}
    for row in READINGS:
        latest[row["station_id"]] = row
    return {"stations": len(latest), "readings": list(latest.values())}


@app.get("/detections")
async def detections(min_confidence: float = 0.0):
    """CPB detections from the classifier (mocked with the June flight)."""
    if not 0.0 <= min_confidence <= 1.0:
        raise HTTPException(status_code=422, detail="min_confidence must be in [0, 1]")
    hits = [d for d in DETECTIONS if d["confidence"] >= min_confidence]
    return {"count": len(hits), "detections": hits}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Read-only farmer dashboard (mock data) — the MS3 dashboard preview."""
    return render_dashboard(DETECTIONS)


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
    import uvicorn

    # Port 80 by default so the Dockerfile and its HEALTHCHECK keep working;
    # override locally, where binding 80 needs privileges.
    uvicorn.run(
        app,
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", os.getenv("PORT", "80"))),
    )
