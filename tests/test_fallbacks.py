"""Degradation: with no database reachable, every endpoint still answers.

The suite runs with DB_ENABLED=0 (see conftest), which is also what CI uses.
"""

from fastapi.testclient import TestClient

from app import app

client = TestClient(app)


def test_health_never_depends_on_the_database():
    """The Docker HEALTHCHECK calls this; it must not follow the DB path."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_readings_latest_falls_back_to_memory():
    body = client.get("/readings/latest").json()
    assert body["source"] == "memory"
    assert body["synthetic"] is False
    # the original keys are untouched
    assert "stations" in body and "readings" in body


def test_detections_falls_back_to_memory():
    body = client.get("/detections").json()
    assert body["source"] == "memory"
    assert body["count"] == 2
    assert body["detections"][0]["label"] == "colorado_potato_beetle"


def test_detections_still_validates_before_touching_the_database():
    assert client.get("/detections", params={"min_confidence": 1.5}).status_code == 422
    assert client.get("/detections", params={"min_confidence": -0.1}).status_code == 422


def test_stations_falls_back_to_the_mock_grid():
    body = client.get("/stations").json()
    assert body["source"] == "memory"
    assert body["count"] == 9
    # every fallback row labels itself, so it cannot be mistaken for real data
    assert all(row["data_source"] == "mock" for row in body["stations"])
    assert body["stations"][0]["station_id"].startswith("station-")


def test_dashboard_falls_back_to_the_mock_page():
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    # The i18n dictionary is embedded in the page, so check the rendered badge.
    assert 'data-i18n="demo"' in body
    assert 'data-i18n="synthetic"' not in body


def test_every_response_declares_its_provenance():
    for path in ("/readings/latest", "/detections", "/stations"):
        body = client.get(path).json()
        assert set(("source", "schema", "synthetic")).issubset(body), path
