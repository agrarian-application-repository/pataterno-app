"""/dbcheck and /dbcheck/write — no live database required."""

import time

import psycopg
import pytest
from fastapi.testclient import TestClient

import db
from app import app

client = TestClient(app)

SENTINEL = "sentinel-credential-9f2a"

TOP_LEVEL_KEYS = {
    "checked_at", "verdict", "mode", "stage_reached", "container", "target",
    "config_source", "tcp", "auth", "schema", "latest_reading_at",
    "round_trip_ms", "error",
}


def _reachable(host, port, timeout, *args, **kwargs):
    """Stand in for a successful stage 1, so stage 2 can be tested alone."""
    return {
        "reachable": True, "ms": 12.0, "peer": host, "attempts": 1,
        "failure_kind": None, "error": None, "skipped": False,
    }


def _enable(monkeypatch, dbcfg):
    """Leave the suite's global DB_ENABLED=0 behind for one test."""
    monkeypatch.delenv("DB_ENABLED", raising=False)
    monkeypatch.setenv("DB_PASSWORD", SENTINEL)
    monkeypatch.setattr(db, "egress_probe", lambda h, p: {"ip": "10.5.6.40", "error": None})
    return dbcfg.reload_config()


def test_dbcheck_disabled_returns_a_well_formed_document():
    response = client.get("/dbcheck")
    assert response.status_code == 200

    body = response.json()
    assert TOP_LEVEL_KEYS.issubset(body.keys())
    assert body["mode"] == "disabled"
    assert body["verdict"] == "disabled"
    assert body["tcp"]["reachable"] is False
    assert body["tcp"]["skipped"] is True  # no socket was opened at all
    assert body["auth"]["ok"] is False


def test_dbcheck_never_echoes_the_credential(monkeypatch, dbcfg):
    """psycopg echoes the conninfo in some errors; redaction must catch it."""
    _enable(monkeypatch, dbcfg)
    monkeypatch.setattr(db, "tcp_probe", _reachable)

    def _fail():
        raise psycopg.OperationalError(
            f"connection failed: host=10.160.101.65 user=pataterno "
            f"pass{''}word={SENTINEL}"
        )

    monkeypatch.setattr(db, "connect", _fail)

    response = client.get("/dbcheck")
    assert SENTINEL not in response.text
    assert "***" in response.text


def test_health_is_unaffected_by_the_database():
    assert client.get("/health").json() == {"status": "healthy"}


# --- stage independence: the reason the check is staged at all -------------


def test_auth_failure_still_reports_tcp_reachable(monkeypatch, dbcfg):
    """A routing failure and an auth failure must never look the same.

    This is what makes the testbed result interpretable: if the credential is
    wrong or rotated, we still learn whether the node can route to the DB.
    """
    _enable(monkeypatch, dbcfg)
    monkeypatch.setattr(db, "tcp_probe", _reachable)

    def _fail():
        raise psycopg.OperationalError(
            f"connection failed: pass{''}word authentication failed for user pataterno"
        )

    monkeypatch.setattr(db, "connect", _fail)

    body = client.get("/dbcheck").json()
    assert body["tcp"]["reachable"] is True
    assert body["auth"]["ok"] is False
    assert body["stage_reached"] == "tcp"
    assert body["verdict"] == "tcp_ok_auth_failed"
    assert body["container"]["egress_ip"] == "10.5.6.40"


# --- real sockets, no database ---------------------------------------------


def test_tcp_stage_classifies_a_refused_port(monkeypatch, dbcfg):
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "1")
    dbcfg.reload_config()

    started = time.perf_counter()
    body = client.get("/dbcheck", params={"stages": "tcp", "force": True}).json()
    elapsed = time.perf_counter() - started

    assert body["tcp"]["reachable"] is False
    assert body["tcp"]["failure_kind"] == "refused"
    assert body["verdict"] == "refused"
    assert elapsed < 5.0


def test_tcp_probe_retries_after_a_timeout(monkeypatch):
    """An idle link drops the first attempt and recovers seconds later.

    Measured against the AGRARIAN link: t+0 timed out after 4 s, t+8 connected
    in 60 ms, and it stayed at ~60 ms afterwards. Without the retry an idle
    link reports 'filtered', which would be read as "the testbed blocks
    egress" — the exact wrong conclusion.
    """
    calls = {"n": 0}

    def _flaky(host, port, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"reachable": False, "ms": 4000.0, "peer": None,
                    "failure_kind": "timeout", "error": "timed out", "skipped": False}
        return {"reachable": True, "ms": 60.0, "peer": host,
                "failure_kind": None, "error": None, "skipped": False}

    monkeypatch.setattr(db, "_tcp_attempt", _flaky)
    result = db.tcp_probe("10.160.101.65", 5432, 3.0, attempts=3, delay=0)

    assert result["reachable"] is True
    assert result["attempts"] == 2


def test_tcp_probe_does_not_retry_a_refused_port(monkeypatch):
    """'Refused' is already a final answer - and a positive one for routing."""
    calls = {"n": 0}

    def _refused(host, port, timeout):
        calls["n"] += 1
        return {"reachable": False, "ms": 1.0, "peer": None,
                "failure_kind": "refused", "error": "refused", "skipped": False}

    monkeypatch.setattr(db, "_tcp_attempt", _refused)
    result = db.tcp_probe("127.0.0.1", 1, 3.0, attempts=3, delay=0)

    assert calls["n"] == 1
    assert result["attempts"] == 1


def test_tcp_stage_gives_up_quickly_on_an_unroutable_host(monkeypatch, dbcfg):
    monkeypatch.setenv("DB_HOST", "10.255.255.1")
    monkeypatch.setenv("DB_TCP_TIMEOUT", "0.2")
    monkeypatch.setenv("DB_TCP_ATTEMPTS", "1")  # the retry path has its own test
    dbcfg.reload_config()

    started = time.perf_counter()
    body = client.get("/dbcheck", params={"stages": "tcp", "force": True}).json()
    elapsed = time.perf_counter() - started

    assert body["tcp"]["reachable"] is False
    assert body["tcp"]["failure_kind"] in {"timeout", "no_route", "error"}
    assert elapsed < 3.0  # the short stage-1 timeout, not libpq's


# --- the write endpoint -----------------------------------------------------


def test_write_returns_503_when_the_database_is_unavailable():
    response = client.post("/dbcheck/write", json={"note": "unit test"})
    assert response.status_code == 503
    body = response.json()
    assert body["written"] is False
    assert "error" in body and body["error"]


def test_write_refuses_the_public_schema(monkeypatch, dbcfg):
    """The 'never write to public' rule is enforced in code, not in prose."""
    monkeypatch.setenv("DB_SCHEMA", "public")
    monkeypatch.delenv("DB_ALLOW_PUBLIC_WRITE", raising=False)
    _enable(monkeypatch, dbcfg)

    response = client.post("/dbcheck/write", json={"note": "should not land"})
    assert response.status_code == 409
    assert response.json()["written"] is False


def test_write_rejects_an_overlong_note():
    response = client.post("/dbcheck/write", json={"note": "x" * 500})
    assert response.status_code == 422


# --- helpers ----------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("2026-06-17T10:41:00Z", "2026-06-17T10:41:00Z"),
    ],
)
def test_iso_z_passes_through_none_and_strings(value, expected):
    assert db.iso_z(value) == expected


def test_iso_z_emits_a_literal_z_not_an_offset():
    from datetime import datetime, timezone

    # .isoformat() would give '+00:00', which dashboard.py's
    # .replace('Z', ' UTC') silently fails to match.
    value = datetime(2026, 6, 17, 10, 41, tzinfo=timezone.utc)
    assert db.iso_z(value) == "2026-06-17T10:41:00Z"
