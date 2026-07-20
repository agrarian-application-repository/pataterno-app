#!/usr/bin/env python3
"""
pataterno-demo-app - AGRARIAN Project (Open Call 2, Testbed 2)

Demo application for the PATATERNO project (Precision Agriculture for
poTAto pesT Early Recognition with NOn-terrestrial communications).

It exposes a minimal version of the PATATERNO container API described in
the WP4 architecture: soil-reading ingestion, latest-values query and a
Colorado-potato-beetle (CPB) detection summary. Data is kept in memory —
the production version reads/writes the AGRARIAN PostgreSQL instead.

Developed by Buontech Solutions srl.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from dashboard import render_dashboard

APP_NAME = "pataterno-demo-app"
APP_VERSION = "1.0.0"

app = FastAPI(title=APP_NAME, version=APP_VERSION)

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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=80)
