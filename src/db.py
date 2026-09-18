"""
PostgreSQL connector and reachability diagnostics for the PATATERNO container.

The point of this module is to answer one question from wherever the container
happens to run: can it reach the AGRARIAN database, and if not, why not. The
check runs in three independent stages

    1. TCP     raw socket, no credentials  -> proves routing
    2. auth    libpq connect               -> proves the credential
    3. schema  versions, table counts      -> proves the data is there

so that a routing failure is never reported as an authentication failure. Stage
1 works even with no credential configured at all.

Nothing here runs at import time.
"""

from __future__ import annotations

import contextlib
import errno
import os
import socket
import threading
import time
from datetime import datetime, timezone

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

import dbconfig
from dbconfig import get_config, redact

DIAGNOSTIC_TABLE = "container_pings"

_conn: psycopg.Connection | None = None
_lock = threading.RLock()
_cooldown_until = 0.0
_last_write = 0.0

# Windows reports these as winerror; POSIX as errno.
_NO_ROUTE_ERRNOS = {errno.ENETUNREACH, errno.EHOSTUNREACH}
_NO_ROUTE_WINERRORS = {10051, 10065}


class DbUnavailable(RuntimeError):
    """Raised when a database operation cannot be served."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def utc_now_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_z(value) -> str | None:
    """Render a timestamp as ISO-8601 with a literal Z.

    `datetime.isoformat()` emits '+00:00', which the dashboard's
    `.replace('Z', ' UTC')` silently fails to match, so it is not used.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def _classify(exc: BaseException) -> str:
    """Why did the socket fail? The three cases need three different emails."""
    if isinstance(exc, socket.gaierror):
        return "dns"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    if isinstance(exc, OSError):
        if exc.errno in _NO_ROUTE_ERRNOS:
            return "no_route"
        if getattr(exc, "winerror", None) in _NO_ROUTE_WINERRORS:
            return "no_route"
    return "error"


def _note_failure() -> None:
    global _cooldown_until
    _cooldown_until = time.monotonic() + get_config().cooldown_s


def _note_success() -> None:
    global _cooldown_until
    _cooldown_until = 0.0


def cooling_down() -> bool:
    return time.monotonic() < _cooldown_until


def db_available() -> bool:
    """True when a query is worth attempting.

    The cooldown is what keeps a dead database costing one timeout rather than
    one timeout per request.
    """
    return get_config().enabled and not cooling_down()


# ---------------------------------------------------------------------------
# Stage 1 - reachability, no credentials
# ---------------------------------------------------------------------------


def egress_probe(host: str, port: int) -> dict:
    """Local address the kernel would use to reach `host`. Sends no packet."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((host, port))
        return {"ip": sock.getsockname()[0], "error": None}
    except OSError as exc:
        # "Network is unreachable" here is itself a strong finding.
        return {"ip": None, "error": redact(exc)}
    finally:
        sock.close()


def _tcp_attempt(host: str, port: int, timeout: float) -> dict:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            peer = sock.getpeername()[0]
        elapsed = (time.perf_counter() - started) * 1000.0
        return {
            "reachable": True,
            "ms": round(elapsed, 1),
            "peer": peer,
            "failure_kind": None,
            "error": None,
            "skipped": False,
        }
    except Exception as exc:  # noqa: BLE001 - a diagnostic must not raise
        elapsed = (time.perf_counter() - started) * 1000.0
        return {
            "reachable": False,
            "ms": round(elapsed, 1),
            "peer": None,
            "failure_kind": _classify(exc),
            "error": redact(exc),
            "skipped": False,
        }


def tcp_probe(
    host: str,
    port: int,
    timeout: float,
    attempts: int = 3,
    delay: float = 2.0,
) -> dict:
    """Open a bare TCP connection. No credential involved, so this isolates
    routing from authentication.

    Timeouts are retried **with a pause in between**, because the AGRARIAN link
    goes dead when idle and takes roughly 10-30 s of repeated attempts to come
    back. Measured 18 Sep 2026 from the laptop, interleaving .NET and Python so
    the runtime was ruled out: both fail together at t+0, both succeed together
    at ~t+20, and it then stays at ~60 ms until it next goes idle. The
    `PersistentKeepalive = 21` in the tunnel profile does not prevent it.

    Consequence for the testbed run: a single probe that reports a timeout
    proves nothing. Only `attempts` exhausted over the full window is evidence,
    and even then it is worth re-running.

    Without this, an idle link reports `filtered`, which reads as "the testbed
    blocks egress": the exact wrong answer to the question this probe exists to
    settle. A refused port is not retried - that answer is already final, and a
    positive result for routing.
    """
    attempts = max(1, attempts)
    result = _tcp_attempt(host, port, timeout)
    result["attempts"] = 1
    for attempt in range(2, attempts + 1):
        if result["reachable"] or result["failure_kind"] not in {"timeout", "no_route"}:
            return result
        if delay > 0:
            time.sleep(delay)
        result = _tcp_attempt(host, port, timeout)
        result["attempts"] = attempt
    return result


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def connect() -> psycopg.Connection:
    """Return the shared connection, opening it if needed. Raises on failure."""
    global _conn
    cfg = get_config()
    if not cfg.enabled:
        raise DbUnavailable(f"database disabled (mode={cfg.mode})")

    with _lock:
        if _conn is not None and not _conn.closed:
            return _conn
        conn = psycopg.connect(row_factory=dict_row, autocommit=True, **cfg.conn_kwargs())
        try:
            with conn.cursor() as cur:
                # search_path must include public: PostGIS lives there, so
                # ST_AsGeoJSON/ST_X would not resolve with the schema alone.
                cur.execute(
                    sql.SQL("SET search_path TO {}, public").format(sql.Identifier(cfg.schema))
                )
                cur.execute(
                    sql.SQL("SET statement_timeout = {}").format(
                        sql.Literal(cfg.statement_timeout_ms)
                    )
                )
                # Without this, date_trunc('hour', ...) silently shifts buckets.
                cur.execute("SET TIME ZONE 'UTC'")
        except Exception:
            conn.close()
            raise
        _conn = conn
        return _conn


@contextlib.contextmanager
def session():
    """Exclusive use of the shared connection.

    A psycopg connection is not safe for concurrent use: two threads on one
    connection produce "consuming input failed: server closed the connection
    unexpectedly". Locking only the creation of the connection is not enough -
    the whole execute/fetch cycle has to be serialised, otherwise the
    background keepalive collides with a request (or two dashboard viewers
    collide with each other). Traffic here is tiny, so one connection under a
    lock is the right trade; a pool is the answer only if that changes.
    """
    with _lock:
        yield connect()


def close() -> None:
    """Shutdown hook. Never raises."""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:  # noqa: BLE001
                pass
            _conn = None


def _ping() -> bool:
    with session() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
    return True


def keepalive() -> bool:
    """Keep the database path warm, and recover it when it drops. Never raises.

    Deliberately NOT gated on db_available(): that returns False while the
    circuit breaker is cooling down, and this is the mechanism that clears the
    breaker. Gating it here would mean the app could never recover on its own.

    On failure it fires the multi-attempt TCP probe before retrying, because
    the AGRARIAN link needs a burst of attempts to wake - a single lost ping
    every interval would never bring it back.
    """
    cfg = get_config()
    if not cfg.enabled:
        return False

    try:
        _ping()
        _note_success()
        return True
    except Exception:  # noqa: BLE001
        _reset()

    probe = tcp_probe(cfg.host, cfg.port, cfg.tcp_timeout, cfg.tcp_attempts, cfg.tcp_retry_delay)
    if not probe["reachable"]:
        _note_failure()
        return False

    try:
        _ping()
        _note_success()
        return True
    except Exception:  # noqa: BLE001
        _reset()
        _note_failure()
        return False


def _reset() -> None:
    """Drop a connection that has failed, so the next call reconnects."""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:  # noqa: BLE001
                pass
            _conn = None


# ---------------------------------------------------------------------------
# Stage 2 and 3
# ---------------------------------------------------------------------------

_SERVER_INFO_SQL = """
SELECT version()                      AS server_version,
       current_database()             AS dbname,
       current_user                   AS db_user,
       current_setting('search_path') AS search_path,
       current_setting('TimeZone')    AS timezone,
       inet_client_addr()::text       AS client_addr,
       (SELECT extversion FROM pg_extension WHERE extname = 'postgis') AS postgis,
       (SELECT n.nspname FROM pg_extension e
          JOIN pg_namespace n ON n.oid = e.extnamespace
         WHERE e.extname = 'postgis') AS postgis_schema
"""

_TABLES_SQL = """
SELECT c.relname AS table_name
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %(schema)s
  AND c.relkind IN ('r', 'p')
ORDER BY c.relname
"""


def _table_counts(cur, schema: str, names: list[str]) -> dict[str, int]:
    """Exact counts for the tables that exist.

    Built from the discovered names rather than a fixed list: a single
    UNION ALL touching a missing table aborts the whole statement, and
    container_pings does not exist until the first write.
    """
    if not names:
        return {}
    parts = [
        sql.SQL("SELECT {} AS t, count(*) AS n FROM {}.{}").format(
            sql.Literal(name), sql.Identifier(schema), sql.Identifier(name)
        )
        for name in names[:20]
    ]
    cur.execute(sql.SQL(" UNION ALL ").join(parts))
    return {row["t"]: int(row["n"]) for row in cur.fetchall()}


def run_dbcheck(stages: str = "all", force: bool = False) -> dict:
    """Full diagnostic. Never raises - a check that crashes tells you nothing."""
    cfg = get_config()
    started = time.perf_counter()

    doc: dict = {
        "checked_at": utc_now_z(),
        "verdict": "unknown",
        "mode": cfg.mode,
        "stage_reached": "none",
        "container": {
            "hostname": socket.gethostname(),
            "egress_ip": None,
            "egress_error": None,
            "image_tag": os.getenv("IMAGE_TAG", "unknown"),
        },
        "target": cfg.public_dict(),
        "config_source": dbconfig.config_source(cfg),
        "tcp": {"reachable": False, "ms": None, "peer": None, "failure_kind": None,
                "error": None, "skipped": True},
        "auth": {"ok": False, "ms": None, "server_version": None, "postgis": None,
                 "postgis_schema": None, "client_addr_seen_by_server": None,
                 "db_user": None, "search_path": None, "error": None, "skipped": True},
        "schema": {"ok": False, "search_path": None, "tables": {}, "error": None,
                   "skipped": True},
        "latest_reading_at": None,
        "round_trip_ms": None,
        "error": None,
    }

    if cfg.mode == "disabled" and not force:
        doc["verdict"] = "disabled"
        doc["error"] = "database disabled (DB_ENABLED=0)"
        doc["round_trip_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
        return doc

    # ---- stage 1 --------------------------------------------------------
    egress = egress_probe(cfg.host, cfg.port)
    doc["container"]["egress_ip"] = egress["ip"]
    doc["container"]["egress_error"] = egress["error"]

    doc["tcp"] = tcp_probe(
        cfg.host, cfg.port, cfg.tcp_timeout, cfg.tcp_attempts, cfg.tcp_retry_delay
    )
    doc["stage_reached"] = "tcp"

    if not doc["tcp"]["reachable"]:
        kind = doc["tcp"]["failure_kind"]
        doc["verdict"] = {
            "timeout": "filtered",
            "refused": "refused",
            "no_route": "no_route",
            "dns": "dns",
        }.get(kind, "unreachable")
        doc["error"] = doc["tcp"]["error"]
        doc["round_trip_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
        return doc

    if stages == "tcp":
        doc["verdict"] = "tcp_ok"
        doc["round_trip_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
        return doc

    if cfg.mode == "no_credentials":
        doc["auth"]["error"] = "no credentials configured"
        doc["verdict"] = "no_credentials"
        doc["round_trip_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
        return doc

    # ---- stage 2 --------------------------------------------------------
    auth_started = time.perf_counter()
    try:
        with session() as conn:
            with conn.cursor() as cur:
                cur.execute(_SERVER_INFO_SQL)
                info = cur.fetchone() or {}
        doc["auth"].update(
            {
                "ok": True,
                "ms": round((time.perf_counter() - auth_started) * 1000.0, 1),
                "server_version": info.get("server_version"),
                "postgis": info.get("postgis"),
                "postgis_schema": info.get("postgis_schema"),
                "client_addr_seen_by_server": info.get("client_addr"),
                "db_user": info.get("db_user"),
                "search_path": info.get("search_path"),
                "skipped": False,
            }
        )
        doc["stage_reached"] = "auth"
        _note_success()
    except Exception as exc:  # noqa: BLE001
        _reset()
        _note_failure()
        doc["auth"].update(
            {
                "ok": False,
                "ms": round((time.perf_counter() - auth_started) * 1000.0, 1),
                "error": redact(exc, cfg),
                "skipped": False,
            }
        )
        doc["verdict"] = "tcp_ok_auth_failed"
        doc["error"] = redact(exc, cfg)
        doc["round_trip_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
        return doc

    if stages == "auth":
        doc["verdict"] = "db_ok"
        doc["round_trip_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
        return doc

    # ---- stage 3 --------------------------------------------------------
    try:
        with session() as conn:
            with conn.cursor() as cur:
                cur.execute(_TABLES_SQL, {"schema": cfg.schema})
                names = [row["table_name"] for row in cur.fetchall()]
                counts = _table_counts(cur, cfg.schema, names)
                if "readings" in names:
                    cur.execute("SELECT max(measured_at) AS latest FROM readings")
                    row = cur.fetchone() or {}
                    doc["latest_reading_at"] = iso_z(row.get("latest"))
        doc["schema"].update(
            {
                "ok": bool(names),
                "search_path": doc["auth"]["search_path"],
                "tables": counts,
                "skipped": False,
            }
        )
        doc["stage_reached"] = "schema"
        doc["verdict"] = "db_ok" if names else "tcp_ok_schema_missing"
    except Exception as exc:  # noqa: BLE001
        _reset()
        _note_failure()
        doc["schema"].update({"ok": False, "error": redact(exc, cfg), "skipped": False})
        doc["verdict"] = "tcp_ok_schema_missing"
        doc["error"] = redact(exc, cfg)

    doc["round_trip_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
    return doc


# ---------------------------------------------------------------------------
# The proof row
# ---------------------------------------------------------------------------

_CREATE_PINGS_SQL = """
CREATE TABLE IF NOT EXISTS container_pings (
    ping_id   bigserial PRIMARY KEY,
    pinged_at timestamptz NOT NULL DEFAULT now(),
    hostname  text,
    egress_ip text,
    image_tag text,
    note      text
)
"""

_INSERT_PING_SQL = """
INSERT INTO container_pings (hostname, egress_ip, image_tag, note)
VALUES (%(hostname)s, %(egress_ip)s, %(image_tag)s, %(note)s)
RETURNING ping_id, pinged_at
"""


def _query(statement: str, params: dict | None = None) -> list[dict]:
    """Run a read query and return rows. Raises DbUnavailable on any failure."""
    if not db_available():
        raise DbUnavailable("database unavailable")
    try:
        with session() as conn:
            with conn.cursor() as cur:
                cur.execute(statement, params or {})
                rows = cur.fetchall()
        _note_success()
        return rows
    except Exception as exc:  # noqa: BLE001
        _reset()
        _note_failure()
        raise DbUnavailable(redact(exc)) from None


# --- stations ---------------------------------------------------------------

_STATIONS_SQL = """
SELECT s.station_id,
       s.field_id,
       s.kind,
       s.label,
       ST_AsGeoJSON(s.geom)::json AS geometry,
       ST_Y(s.geom)::float8       AS lat,
       ST_X(s.geom)::float8       AS lon,
       s.installed_at,
       s.data_source
FROM stations s
WHERE (%(kind)s::text IS NULL OR s.kind = %(kind)s::text)
ORDER BY (s.kind <> 'soil_station'), s.station_id
"""


def fetch_stations(kind: str | None = None) -> list[dict]:
    """All stations, soil stations first. `kind='soil_station'` drops gateways."""
    rows = _query(_STATIONS_SQL, {"kind": kind})
    for row in rows:
        row["installed_at"] = iso_z(row.get("installed_at"))
    return rows


# --- readings ---------------------------------------------------------------

# DISTINCT ON with a matching leading ORDER BY, so this rides
# readings_station_time_idx (station_id, measured_at DESC).
_LATEST_READINGS_SQL = """
SELECT DISTINCT ON (r.station_id)
       r.station_id,
       s.label,
       r.measured_at,
       r.moisture_pct, r.temperature_c, r.ec_us_cm, r.ph,
       r.nitrogen_mg_kg, r.phosphorus_mg_kg, r.potassium_mg_kg,
       r.battery_v, r.rssi_dbm, r.snr_db,
       r.data_source
FROM readings r
JOIN stations s ON s.station_id = r.station_id
WHERE s.kind = 'soil_station'
ORDER BY r.station_id, r.measured_at DESC
"""


def fetch_latest_readings() -> list[dict]:
    """Latest reading per soil station (gateways excluded)."""
    rows = _query(_LATEST_READINGS_SQL)
    for row in rows:
        row["measured_at"] = iso_z(row.get("measured_at"))
        # the in-memory path carries received_at; keep the shape identical
        row["received_at"] = row["measured_at"]
    return rows


# The window is anchored on the newest reading, not on now(): the synthetic
# dataset ends 2026-09-08, and anchoring on the wall clock returns an empty
# chart. DB_SERIES_ANCHOR=now switches this once the gateway feeds live data.
_SERIES_SQL = """
WITH soil AS (
    SELECT station_id FROM stations WHERE kind = 'soil_station'
),
anchor AS (
    SELECT date_trunc('hour', max(r.measured_at)) AS t_end
    FROM readings r JOIN soil s ON s.station_id = r.station_id
)
SELECT date_trunc('hour', r.measured_at) AS hour_utc,
       avg(r.moisture_pct)::float8       AS moisture_pct,
       avg(r.temperature_c)::float8      AS temperature_c,
       count(*)                          AS samples
FROM readings r
JOIN soil s ON s.station_id = r.station_id
CROSS JOIN anchor a
WHERE r.measured_at >= a.t_end - make_interval(hours => %(hours)s::int - 1)
  AND r.measured_at <  a.t_end + interval '1 hour'
GROUP BY 1
ORDER BY 1
"""


def fetch_moisture_series(hours: int = 24) -> list[dict]:
    """Hourly field-average moisture, newest `hours` buckets.

    Readings arrive every 10 minutes, so each bucket averages up to 9 stations
    x 6 samples. Outages mean fewer than `hours` rows may come back - the
    renderer must cope rather than assume a fixed length.
    """
    rows = _query(_SERIES_SQL, {"hours": hours})
    series = []
    for row in rows:
        stamp = row["hour_utc"]
        series.append(
            {
                "hour": stamp.strftime("%H:%M") if hasattr(stamp, "strftime") else str(stamp),
                "hour_utc": iso_z(stamp),
                "value": row.get("moisture_pct"),
                "temperature_c": row.get("temperature_c"),
                "samples": int(row.get("samples") or 0),
            }
        )
    return series


# --- detections -------------------------------------------------------------

# Every field-name divergence is resolved here in SQL (class_label -> label,
# image_ref -> frame, geometry -> lat/lon), so the Python side only has to fix
# timestamps. COALESCE matters: confidence is nullable and the dashboard
# multiplies it by 100.
_DETECTIONS_SQL = """
SELECT d.detection_id,
       d.flight_id,
       f.flown_at,
       d.captured_at,
       COALESCE(d.class_label, 'colorado_potato_beetle') AS label,
       COALESCE(d.image_ref, '')                         AS frame,
       COALESCE(d.confidence, 0)::float8                 AS confidence,
       ST_Y(d.geom)::float8                              AS lat,
       ST_X(d.geom)::float8                              AS lon,
       d.sector, d.life_stage, d.count_n,
       d.density_per_m2::float8                          AS density_per_m2,
       d.data_source
FROM detections d
JOIN flights f ON f.flight_id = d.flight_id
WHERE COALESCE(d.confidence, 0) >= %(min_confidence)s
  {flight_filter}
ORDER BY f.flown_at DESC, d.captured_at DESC, d.detection_id DESC
LIMIT %(limit)s
"""

_LATEST_FLIGHT_FILTER = (
    "AND f.flight_id = (SELECT flight_id FROM flights ORDER BY flown_at DESC LIMIT 1)"
)


def fetch_detections(
    min_confidence: float = 0.0,
    limit: int = 200,
    latest_flight_only: bool = False,
) -> list[dict]:
    """CPB detections, newest flight first, already in the API's field names."""
    statement = _DETECTIONS_SQL.format(
        flight_filter=_LATEST_FLIGHT_FILTER if latest_flight_only else ""
    )
    rows = _query(statement, {"min_confidence": min_confidence, "limit": limit})
    for row in rows:
        row["captured_at"] = iso_z(row.get("captured_at"))
        row["flown_at"] = iso_z(row.get("flown_at"))
    return rows


def dashboard_bundle() -> dict | None:
    """Everything the dashboard needs, all-or-nothing.

    A half-real dashboard (real stations, mock chart) cannot be captioned
    honestly, so any failure falls back to the complete mock instead.
    """
    try:
        stations = fetch_stations(kind="soil_station")
        readings = fetch_latest_readings()
        series = fetch_moisture_series()
        detections = fetch_detections(latest_flight_only=True, limit=50)
    except DbUnavailable:
        return None
    except Exception:  # noqa: BLE001
        return None

    rows = stations + readings + detections
    synthetic = get_config().schema != "public" or any(
        row.get("data_source") == "synthetic" for row in rows
    )
    return {
        "stations": stations,
        "readings": readings,
        "series": series,
        "detections": detections,
        "as_of": readings[0]["measured_at"] if readings else None,
        "synthetic": synthetic,
        "schema": get_config().schema,
    }


def write_ping(note: str | None = None) -> dict:
    """Insert one marker row into <schema>.container_pings and return it."""
    global _last_write
    cfg = get_config()

    if not cfg.enabled:
        raise DbUnavailable(f"database disabled (mode={cfg.mode})")
    if cfg.schema == "public" and os.getenv("DB_ALLOW_PUBLIC_WRITE", "0") != "1":
        # Enforces the spec's "never write to public" in code, not in prose.
        raise PermissionError("refusing to write to schema public")

    now = time.monotonic()
    if now - _last_write < 1.0:
        raise DbUnavailable("rate limited: one ping per second")

    egress = egress_probe(cfg.host, cfg.port)
    params = {
        "hostname": socket.gethostname(),
        "egress_ip": egress["ip"],
        "image_tag": os.getenv("IMAGE_TAG", "unknown"),
        "note": (note or "")[:200] or None,
    }

    try:
        with session() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(_CREATE_PINGS_SQL)
                    cur.execute(_INSERT_PING_SQL, params)
                    row = cur.fetchone() or {}
        _note_success()
        _last_write = now
    except Exception as exc:  # noqa: BLE001
        _reset()
        _note_failure()
        raise DbUnavailable(redact(exc, cfg)) from None

    return {
        "ping_id": row.get("ping_id"),
        "pinged_at": iso_z(row.get("pinged_at")),
        "schema": cfg.schema,
        "hostname": params["hostname"],
        "egress_ip": params["egress_ip"],
        "image_tag": params["image_tag"],
        "note": params["note"],
    }
