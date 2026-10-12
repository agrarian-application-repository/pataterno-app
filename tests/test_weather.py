"""Weather-window job: pure window logic, plus the no-network path."""

from fastapi.testclient import TestClient

import weather
from app import app
from dashboard import render_dashboard

client = TestClient(app)


def _hourly(n=96, rain_at=(), wind_at=(), night=range(0, 6)):
    """n hours from midnight: day 06-19, calm, 18 C, dry unless told otherwise."""
    hours = [f"2026-10-{13 + i // 24:02d}T{i % 24:02d}:00" for i in range(n)]
    return {
        "time": hours,
        "is_day": [0 if (i % 24) in night or (i % 24) >= 19 else 1 for i in range(n)],
        "precipitation": [2.0 if i in rain_at else 0.0 for i in range(n)],
        "precipitation_probability": [90 if i in rain_at else 5 for i in range(n)],
        "wind_speed_10m": [30.0 if i in wind_at else 6.0 for i in range(n)],
        "temperature_2m": [18.0] * n,
    }


def test_dry_calm_days_give_morning_window():
    out = weather.compute_windows(_hourly())
    assert out["best"]["start"] == "2026-10-13T06:00"
    # spray 2 h must end before dusk: last start 17:00
    assert out["best"]["latest_start"] == "2026-10-13T17:00"
    assert out["rejected"]["night"] > 0


def test_rain_within_dry_period_blocks_window():
    # rain at 20:00 on day 1 -> no start on day 1 can have 24 dry hours after
    out = weather.compute_windows(_hourly(rain_at={20}))
    assert not out["best"]["start"].startswith("2026-10-13")
    assert out["rejected"].get("rain_after", 0) > 0


def test_wind_blocks_spray_hours():
    out = weather.compute_windows(_hourly(wind_at=set(range(6, 19))))
    assert not out["best"]["start"].startswith("2026-10-13")
    assert out["rejected"]["wind"] > 0


def test_no_window_when_always_raining():
    out = weather.compute_windows(_hourly(rain_at=set(range(96))))
    assert out["best"] is None and out["windows"] == []


def test_rules_from_env(monkeypatch):
    monkeypatch.setenv("WEATHER_DRY_HOURS", "6")
    monkeypatch.setenv("WEATHER_WIND_MAX_KMH", "10")
    r = weather.WindowRules.from_env()
    assert r.dry_hours == 6 and r.wind_max_kmh == 10.0


def test_endpoint_without_network(monkeypatch):
    def boom(*a, **k):
        raise OSError("no egress")

    monkeypatch.setattr(weather, "fetch_forecast", boom)
    weather._cache.clear()
    body = client.get("/weather/window?refresh=true").json()
    assert body["available"] is False and "no egress" in body["error"]


def test_endpoint_with_stubbed_forecast(monkeypatch):
    monkeypatch.setattr(weather, "fetch_forecast", lambda *a, **k: {"timezone": "Europe/Rome", "hourly": _hourly()})
    weather._cache.clear()
    body = client.get("/weather/window?refresh=true").json()
    assert body["available"] is True
    assert body["best"]["start"] == "2026-10-13T06:00"
    assert "hourly" not in body
    assert "hourly" in client.get("/weather/window?hourly=true").json()


def test_dashboard_shows_live_window_outside_i18n():
    window = {"best": {"start": "2026-10-13T08:00", "spray_until": "2026-10-13T10:00",
                       "max_wind_kmh": 12.0, "dry_until": "2026-10-14T09:00"},
              "windows": []}
    page = render_dashboard([], meta={"window": window})
    assert "13/10 08:00–10:00" in page
    # the real times must not sit inside a data-i18n span (setLang would erase them)
    assert 'data-i18n="window_text"' not in page
