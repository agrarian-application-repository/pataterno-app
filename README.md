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
| GET | `/stations` | Station inventory with GeoJSON geometry (`?kind=soil_station` drops gateways) |
| GET | `/dbcheck` | Is the AGRARIAN database reachable from here, and if not, why not (see Database) |
| POST | `/dbcheck/write` | Write one marker row into `<schema>.container_pings` |
| GET | `/model` | The shipped INT8 classifier: artefact, size, reference accuracy, runtime, ROI settings |
| POST | `/images?frame=&lat=&lon=` | Whole drone frame as the request body -> candidate regions -> INT8 classifier -> detections, with the frame's KPI-6 byte figures |
| POST | `/images/summary` | P0 from a ground gateway: sub-kilobyte "candidates at these positions" alert |
| POST | `/images/package` | P1: the candidate crops (JPEG, base64) -> classified here -> detections |
| POST | `/images/preview` | P2: low-resolution frame preview (sent only when the link has room) |
| GET | `/alerts` | Per-frame alerts with their timing fields (KPI-2 instrumentation) |
| GET | `/weather/window` | Rain-free treatment windows over the next 96 h (Open-Meteo forecast for the field) |

`/readings/latest`, `/detections`, `/stations` and `/dashboard` read from the
database when it is reachable and fall back to the built-in demo data
otherwise. Every JSON response says which happened:

```json
{ "source": "db" | "memory", "schema": "dev", "synthetic": true }
```

`synthetic` is true whenever the rows come from the `dev` sandbox, and the
dashboard shows a matching amber badge — so neither an API consumer nor a
screenshot can mistake sandbox data for field data.

## Image pipeline and the two roles (v1.2)

One image runs both ends of the satellite link:

- `ROLE=edge` (default) - the API above. The classifier is the KPI-7 artefact:
  EfficientNet-B0, OpenVINO NNCF INT8, `models/cpb_int8/` (6.12 MB weights).
  `tools/eval_model.py <dir>` re-measures it with the container's own
  preprocessing; on the 448-image held-out set it gives 96.21% (211/223 CPB,
  220/225 not-CPB), identical to the KPI-7 report.
- `ROLE=producer` - the ground gateway data producer (`src/producer.py`). For
  each frame in `FRAMES_DIR` it keeps only candidate regions (saturated
  orange-brown specks with leaf around them, `src/vision.py`), queues P0
  summary / P1 crops / P2 preview, and sends them to `EDGE_URL` highest
  priority first, holding them while the link is down. It writes per-frame
  KPI-6 bytes and KPI-2 timings to `RESULTS_PATH`.

```bash
docker run -d --name edge -p 8080:80 -e DB_ENABLED=0 ghcr.io/agrarian-application-repository/pataterno-app:latest
docker run --rm -e ROLE=producer -e EDGE_URL=http://<edge-host>:8080   -e FRAMES_DIR=/frames -e RESULTS_PATH=/frames/results.json -v $PWD/frames:/frames   ghcr.io/agrarian-application-repository/pataterno-app:latest
```

Known limit: the classifier was trained on close-up images. On random 224 px
crops of drone frames it flags 18-42% as CPB (bare soil, mixed backgrounds),
which is why the ground-side candidate filter is required in front of it; the
fine-tuning on the season's annotated field crops is the next model step.

`testbed/` holds the local satellite testbed (OpenSAND, the emulator NCSRD
documents for the Open Call testbeds) used to run both roles across an
emulated satellite link; see `testbed/opensand/README.md`.

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
| `DB_COOLDOWN_S` | `15` | after a failure, skip the database for this long rather than pay the timeout on every request |
| `DB_KEEPALIVE_S` | `0` | background ping to keep an idle-dropping link warm; off by default (see below) |
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

### Phone-home (reading the verdict without a shell)

The Agrarian Portal shows pod status but offers no log viewer, and a container
that exposes no reachable port cannot be curled. With no credential (so no
`container_pings` row either), there is no way to read the `/dbcheck` verdict
from a portal deployment. Phone-home is the credential-free out-of-band channel:
on startup the container POSTs the very same diagnostic to a listener you run.

It carries **no credential** - the diagnostic redacts secrets, and in the
default credential-free mode it is a pure TCP-reachability result (hostname,
egress IP, whether the database answered). The container is simply reporting its
own connectivity to its owner. The token is a shared secret so the listener can
ignore unrelated internet noise on its open port; it authenticates nothing.

Enabled only when `PHONE_HOME_URL` is set:

| Variable | Meaning |
|---|---|
| `PHONE_HOME_URL` | e.g. `http://<your-wan-ip>:48080/beacon`; unset disables it |
| `PHONE_HOME_TOKEN` | shared secret; the listener ignores requests without it |
| `PHONE_HOME_ATTEMPTS` / `PHONE_HOME_RETRY_DELAY` | retries for the idle link (default 5 / 3 s) |

The listener (`tools/phone_home_listener.py`) only receives and prints text; it
never executes anything it is sent.

```bash
# 1. run the listener (any machine the container can reach)
python tools/phone_home_listener.py --port 48080 --token <shared-secret>

# 2. run the container pointed at it. DB_ENABLED=0 tests the beacon alone,
#    without touching the database:
docker run --rm \
  -e DB_ENABLED=0 \
  -e PHONE_HOME_URL=http://<listener-host>:48080/beacon \
  -e PHONE_HOME_TOKEN=<shared-secret> \
  ghcr.io/agrarian-application-repository/pataterno-app:latest
```

For a real Testbed 2 run the listener must be reachable from that network,
which for a home connection means forwarding a port on your router - e.g.
`upnpc -a <your-lan-ip> 48080 48080 TCP` (delete afterwards with
`upnpc -d 48080 TCP`). A single beacon that does not arrive proves nothing on
this flaky link; wait for the retries.

### Known issue: the VPN link idles out

Measured from the development laptop on 18 Sep 2026: connections to the
database fail for the first ~10–30 s after the link has been idle, then succeed
in ~60 ms and stay fast. It is the link, not this code — .NET and Python
sockets were interleaved and fail together, succeed together — and the
`PersistentKeepalive = 21` in the WireGuard profile does not prevent it.

Two consequences:

- `/dbcheck` retries its TCP stage with a delay (`DB_TCP_ATTEMPTS`,
  `DB_TCP_RETRY_DELAY`) and reports `attempts`. **A single timeout proves
  nothing** — do not read one `filtered` verdict as "the network blocks this".
- A data request that lands in a dead window falls back to the demo data and
  logs one `DBFALLBACK` line saying why. `DB_KEEPALIVE_S=20` enables a
  background ping that keeps the link warm, but it is **off by default**: it
  could not be validated over this link, and the portal reaches the database on
  NCSRD's own network, where the problem is not expected to exist.
