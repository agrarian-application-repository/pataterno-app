"""
Weather-window job: when can the farmer treat so the product is not washed off?

Input is the 96-hour hourly forecast for the field from Open-Meteo (free, no
key, no account - so the container needs nothing but internet egress, which
every Agrarian Portal node has). Output is the list of treatment windows: runs
of start hours at which a spray of SPRAY_HOURS can be done in daylight, dry,
with low wind and a workable temperature, AND the following DRY_HOURS stay
rain-free.

All thresholds are environment-configurable. The defaults are a conservative
starting point to be confirmed with the field partner and the product label;
they are not an agronomic prescription.

`compute_windows` is a pure function of the hourly series, so it is tested
without the network; `get_window` adds the fetch and a short cache.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_VARS = (
    "temperature_2m",
    "precipitation",
    "precipitation_probability",
    "wind_speed_10m",
    "relative_humidity_2m",
    "is_day",
)

# Petrizzo field PTZ-01 centre (same point as the dev-schema field polygon).
DEFAULT_LAT = 40.331234
DEFAULT_LON = 15.609152


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass
class WindowRules:
    spray_hours: int = 2          # duration of the application itself
    dry_hours: int = 24           # rain-free hours required after it
    rain_mm_max: float = 0.1      # per hour, during the spray
    dry_total_mm_max: float = 0.2 # summed over the dry period
    prob_max_spray: float = 30.0  # % precipitation probability during the spray
    prob_max_dry: float = 40.0    # % max probability over the dry period
    wind_max_kmh: float = 15.0    # drift limit
    temp_min_c: float = 10.0
    temp_max_c: float = 28.0
    daylight_only: bool = True

    @classmethod
    def from_env(cls) -> "WindowRules":
        r = cls()
        r.spray_hours = int(_f("WEATHER_SPRAY_HOURS", r.spray_hours))
        r.dry_hours = int(_f("WEATHER_DRY_HOURS", r.dry_hours))
        r.wind_max_kmh = _f("WEATHER_WIND_MAX_KMH", r.wind_max_kmh)
        r.temp_min_c = _f("WEATHER_TEMP_MIN_C", r.temp_min_c)
        r.temp_max_c = _f("WEATHER_TEMP_MAX_C", r.temp_max_c)
        r.prob_max_spray = _f("WEATHER_PROB_MAX_SPRAY", r.prob_max_spray)
        r.prob_max_dry = _f("WEATHER_PROB_MAX_DRY", r.prob_max_dry)
        r.daylight_only = os.getenv("WEATHER_DAYLIGHT_ONLY", "1") != "0"
        return r


def _num(v, default=0.0) -> float:
    return default if v is None else float(v)


def _reject_reason(hourly: dict, i: int, r: WindowRules) -> str | None:
    """Why the spray cannot START at hour i (None = it can)."""
    n = len(hourly["time"])
    if i + r.spray_hours + r.dry_hours > n:
        return "beyond_forecast"
    for j in range(i, i + r.spray_hours):
        if r.daylight_only and not _num(hourly["is_day"][j]):
            return "night"
        if _num(hourly["precipitation"][j]) > r.rain_mm_max:
            return "rain"
        if _num(hourly["precipitation_probability"][j]) > r.prob_max_spray:
            return "rain_risk"
        if _num(hourly["wind_speed_10m"][j]) > r.wind_max_kmh:
            return "wind"
        t = _num(hourly["temperature_2m"][j], 20.0)
        if not r.temp_min_c <= t <= r.temp_max_c:
            return "temperature"
    after = range(i + r.spray_hours, i + r.spray_hours + r.dry_hours)
    if sum(_num(hourly["precipitation"][j]) for j in after) > r.dry_total_mm_max:
        return "rain_after"
    if max((_num(hourly["precipitation_probability"][j]) for j in after), default=0) > r.prob_max_dry:
        return "rain_risk_after"
    return None


def compute_windows(hourly: dict, rules: WindowRules | None = None) -> dict:
    """Merge consecutive valid start hours into windows. Pure function."""
    r = rules or WindowRules()
    times = hourly["time"]
    reasons: dict[str, int] = {}
    valid: list[int] = []
    for i in range(len(times)):
        why = _reject_reason(hourly, i, r)
        if why is None:
            valid.append(i)
        else:
            reasons[why] = reasons.get(why, 0) + 1

    windows = []
    run: list[int] = []
    for i in valid:
        if run and i != run[-1] + 1:
            windows.append(run)
            run = []
        run.append(i)
    if run:
        windows.append(run)

    def describe(idx: list[int]) -> dict:
        s, e = idx[0], idx[-1]
        spray_end = min(e + r.spray_hours, len(times) - 1)
        span = range(s, e + r.spray_hours)
        return {
            "start": times[s],
            "latest_start": times[e],
            "spray_until": times[spray_end],
            "start_hours": len(idx),
            "max_wind_kmh": round(max(_num(hourly["wind_speed_10m"][j]) for j in span), 1),
            "temp_range_c": [
                round(min(_num(hourly["temperature_2m"][j]) for j in span), 1),
                round(max(_num(hourly["temperature_2m"][j]) for j in span), 1),
            ],
            "dry_until": times[min(e + r.spray_hours + r.dry_hours - 1, len(times) - 1)],
        }

    described = [describe(w) for w in windows]
    return {
        "windows": described,
        "best": described[0] if described else None,
        "evaluated_start_hours": len(times),
        "rejected": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "rules": asdict(r),
    }


def fetch_forecast(lat: float, lon: float, hours: int = 96, timeout: float = 8.0) -> dict:
    query = urllib.parse.urlencode(
        {
            "latitude": f"{lat:.5f}",
            "longitude": f"{lon:.5f}",
            "hourly": ",".join(HOURLY_VARS),
            "forecast_hours": hours,
            "timezone": "Europe/Rome",
            "wind_speed_unit": "kmh",
        }
    )
    req = urllib.request.Request(f"{OPEN_METEO_URL}?{query}", headers={"User-Agent": "pataterno-app"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


_cache: dict = {}
_cache_lock = threading.Lock()


def get_window(lat: float | None = None, lon: float | None = None, refresh: bool = False) -> dict:
    """Forecast + windows, cached for WEATHER_CACHE_S (default 30 min). Never raises."""
    lat = DEFAULT_LAT if lat is None else lat
    lon = DEFAULT_LON if lon is None else lon
    ttl = _f("WEATHER_CACHE_S", 1800)
    key = (round(lat, 4), round(lon, 4))
    with _cache_lock:
        hit = _cache.get(key)
        if hit and not refresh and time.time() - hit["_at"] < ttl:
            return {**hit["doc"], "cached": True}

    started = time.perf_counter()
    try:
        data = fetch_forecast(lat, lon)
    except Exception as exc:  # noqa: BLE001 - no egress is a normal state
        return {
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
            "provider": "open-meteo.com",
            "location": {"lat": lat, "lon": lon},
        }
    hourly = data.get("hourly") or {}
    result = compute_windows(hourly, WindowRules.from_env())
    doc = {
        "available": True,
        "provider": "open-meteo.com",
        "location": {"lat": lat, "lon": lon, "timezone": data.get("timezone")},
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "fetch_ms": round((time.perf_counter() - started) * 1000, 1),
        "horizon_hours": len(hourly.get("time", [])),
        **result,
        "hourly": {k: hourly.get(k) for k in ("time", "precipitation", "precipitation_probability", "wind_speed_10m", "temperature_2m")},
        "cached": False,
    }
    with _cache_lock:
        _cache[key] = {"_at": time.time(), "doc": doc}
    return doc
