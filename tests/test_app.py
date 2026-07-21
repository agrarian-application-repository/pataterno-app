"""Tests for pataterno-demo-app — run by the CI 'test' job."""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent / "src"))

from fastapi.testclient import TestClient

from app import app

client = TestClient(app)


def test_home():
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "pataterno-demo-app"
    assert body["status"] == "running"


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "healthy"}


def test_ingest_and_latest():
    reading = {
        "station_id": "station-01",
        "moisture_pct": 41.2,
        "temperature_c": 22.8,
        "ec_us_cm": 310.0,
        "ph": 6.7,
        "nitrogen_mg_kg": 48.0,
        "phosphorus_mg_kg": 21.0,
        "potassium_mg_kg": 133.0,
    }
    r = client.post("/readings", json=reading)
    assert r.status_code == 201
    assert r.json()["stored"] is True

    # A newer reading for the same station must replace it in /latest
    reading["moisture_pct"] = 39.9
    client.post("/readings", json=reading)

    r = client.get("/readings/latest")
    assert r.status_code == 200
    body = r.json()
    assert body["stations"] == 1
    assert body["readings"][0]["moisture_pct"] == 39.9


def test_ingest_rejects_bad_station_id():
    r = client.post(
        "/readings",
        json={
            "station_id": "not-a-station",
            "moisture_pct": 40,
            "temperature_c": 20,
            "ec_us_cm": 300,
            "ph": 6.5,
        },
    )
    assert r.status_code == 422


def test_ingest_rejects_out_of_range_ph():
    r = client.post(
        "/readings",
        json={
            "station_id": "station-02",
            "moisture_pct": 40,
            "temperature_c": 20,
            "ec_us_cm": 300,
            "ph": 15.2,
        },
    )
    assert r.status_code == 422


def test_dashboard():
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    assert "PATATERNO" in body
    assert "<svg" in body  # moisture chart present
    # bilingual: Italian rendered by default, English in the toggle dictionary
    assert "Umidità media suolo" in body
    assert "Avg soil moisture" in body
    assert 'data-i18n="avg_moisture"' in body
    assert 'id="btn-en"' in body


def test_detections_filter():
    r = client.get("/detections", params={"min_confidence": 0.95})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["detections"][0]["label"] == "colorado_potato_beetle"

    r = client.get("/detections")
    assert r.json()["count"] == 2
