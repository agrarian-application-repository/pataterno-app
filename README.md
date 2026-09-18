# pataterno-app - AGRARIAN Project

Demo application for the **PATATERNO** project (Precision Agriculture for poTAto pesT Early Recognition with NOn-terrestrial communications). It exposes a minimal version of the PATATERNO container API for the AGRARIAN portal: soil-telemetry ingestion, latest-values query and Colorado-potato-beetle (CPB) detection summary. The application is designed for **Testbed 2 (DLR)** and the potato pest early-recognition use case. It was developed by **Buontech Solutions srl**.

## Overview

This demo exercises the full AGRARIAN application pipeline described in the Developer's Guide:

1. Repository created from the `agrarian-app-template` structure.
2. GitHub Actions CI with three jobs: **test** (pytest + secret scan), **build** (multi-arch Docker image), **package** (publish to GitHub Packages / GHCR and smoke-test the published image).

- **What problem does it solve?** It provides the API surface of the PATATERNO WP4 container: the gateway pushes soil readings, dashboards read the latest values, and the classifier's CPB detections are queryable.
- **Key features:** validated soil-reading ingestion (7-in-1 probe fields: moisture, temperature, EC, pH, NPK), latest-per-station query, detection listing with confidence filter, `/health` endpoint for container orchestration.
- **Edge/satellite/IoT relevance:** mirrors the PATATERNO data path — 9 LoRa soil stations → Raspberry Pi gateway → VPN → AGRARIAN storage — and the INT8 edge classifier's detections. In this demo, storage is in-memory; the production version uses AGRARIAN PostgreSQL (`pataterno_db`).

## API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | App info |
| GET | `/health` | Health check (used by Docker HEALTHCHECK) |
| POST | `/readings` | Ingest a soil reading (validated) |
| GET | `/readings/latest` | Latest reading per station |
| GET | `/detections?min_confidence=0.9` | CPB detections above a confidence threshold |
| GET | `/dashboard` | Farmer dashboard (mock, Italian UI) — station grid, moisture trend, CPB alerts, treatment advice. Preview of the MS3 dashboard. |
| GET | `/dbcheck` | Is the AGRARIAN database reachable from here, and if not, why not (see Database) |
| POST | `/dbcheck/write` | Write one marker row into `<schema>.container_pings` |

## Run locally

```bash
pip install -r requirements.txt
APP_PORT=8000 python src/app.py   # port 80 by default; 80 needs privileges locally
python -m pytest tests/ -v        # the suite never touches a real database
```

## Run the container

```bash
docker run -p 8080:80 ghcr.io/<owner>/pataterno-app:latest
curl http://localhost:8080/health
```

## Database

The container talks to the AGRARIAN PostgreSQL (`pataterno_db`), reachable only
from inside the AGRARIAN WireGuard VPN. Readings and detections are still served
from memory; what is wired up so far is the connector and its diagnostics.

### Configuration

**No credential is stored in this repository or in the image.** Following the
AGRARIAN Database Guide (section 5), the password comes from an environment
variable or a `.env` file at run time. `config/defaults.env` carries only the
non-secret connection parameters.

Precedence, lowest to highest: built-in defaults → `config/defaults.env`
(shipped in the image) → `.env` in the repo root (git-ignored, local
development) → environment → `/app/config/db.env` (operator-mounted override).

| Variable | Default | Meaning |
|---|---|---|
| `DB_ENABLED` | `1` | `0` disables all database access and opens no socket at all |
| `DB_HOST` / `DB_PORT` | `10.160.101.65` / `5432` | AGRARIAN PostgreSQL |
| `DB_NAME` / `DB_USER` | `pataterno_db` / `pataterno` | database and role |
| `DB_PASSWORD` | *(none)* | supplied at run time; without it the app still starts and still probes reachability |
| `DB_SCHEMA` | `dev` | `dev` = synthetic sandbox; `public` = real data, once the gateway writes there |
| `DB_CONNECT_TIMEOUT` / `DB_TCP_TIMEOUT` | `5` / `3` | libpq connect, and the shorter credential-free probe |
| `DB_STATEMENT_TIMEOUT` | `10000` | milliseconds |
| `DB_COOLDOWN_S` | `30` | after a failure, skip the database for this long rather than pay the timeout on every request |
| `APP_PORT` | `80` | listen port |

To supply the credential, copy `config/db.env.example` and either save it as
`.env` in the repo root (local development) or mount it at `/app/config/db.env`
(portal / Kubernetes); `docker run -e DB_PASSWORD=...` works too.

Running **without** a credential is a supported mode, not a failure: the app
starts normally, serves its mock data, and `/dbcheck` still performs its
credential-free TCP stage, reporting `verdict: no_credentials` together with
whether the database is reachable. That is enough to answer the Testbed 2
routing question without any secret leaving your machine.

### Diagnostics

`GET /dbcheck` answers in three independent stages — TCP (no credential, proves
routing), authentication, then schema and row counts — so a routing failure is
never reported as an authentication failure. It always returns HTTP 200; read
`verdict`:

`db_ok` · `tcp_ok_auth_failed` · `tcp_ok_schema_missing` · `no_credentials` ·
`refused` · `filtered` · `no_route` · `disabled`

`container.egress_ip` is the local address the container would use and
`auth.client_addr_seen_by_server` is what the server actually sees; together they
identify which network the container sat on. Parameters: `stages=tcp|auth|all`,
`force=1` (probe even when disabled), `reload=1` (re-read configuration).

The same document is printed once at startup as a single line prefixed
`DBCHECK `, so the verdict is readable with `docker logs` / `kubectl logs` even
when no port is exposed.

`POST /dbcheck/write` inserts one row into `<schema>.container_pings` recording
hostname, egress IP and image tag — the proof that a given deployment can reach
the database. It refuses to write to `public` unless `DB_ALLOW_PUBLIC_WRITE=1`.

```bash
curl -s localhost:8000/dbcheck | python -m json.tool
curl -s -X POST localhost:8000/dbcheck/write \
  -H "Content-Type: application/json" -d '{"note":"laptop"}'
```
