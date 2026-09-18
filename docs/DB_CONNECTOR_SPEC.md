# PATATERNO container: PostgreSQL connector and testbed reachability test

Specification and instructions for the next increment of `pataterno-app`.
Written 2026-09-17 for the session that maintains the container.

**Goal of this increment:** one row written into the AGRARIAN PostgreSQL by the
PATATERNO container while the container runs on Testbed 2 (DLR), plus a
diagnostic endpoint that tells us in one call whether the database is reachable
from wherever the container happens to run.

Everything in section 3 can be built and verified from the laptop today. Only
section 6 needs the testbed.

---

## 1. Facts you must know (all verified 2026-09-17 unless marked)

### 1.1 The database moved
- AGRARIAN PostgreSQL is now **`10.160.101.65:5432`**. The old address
  `10.160.101.177` is dead on every port. It still appears in the AGRARIAN
  Database Guide PDF and in old notes; ignore those.
- Server: PostgreSQL 15.8 (Debian). **PostGIS 3.4.3 is installed** in
  `pataterno_db`. Use native `geometry(...,4326)` columns; no lat/lon fallback
  needed.
- Database `pataterno_db`, user `pataterno`. The credential is **not** in this
  repository: it comes from the AGRARIAN onboarding email (a copy is in
  `tech/VPN portal/readme.txt`) and is supplied at run time — see 1.5 and 3.2.
- User `pataterno` has CREATE on schema `public` and owns schema `dev`.

### 1.2 `public` is empty, `dev` is the sandbox
- `public` contains **no application tables**. The field gateway has never
  landed data there (it still points at the dead .177; the user will fix the
  Raspberry Pi later). **Do not create tables in `public` in this increment.**
- Schema **`dev`** holds a complete synthetic dataset created 2026-09-17 for
  development. Every row has `data_source = 'synthetic'`. It is disposable
  (`DROP SCHEMA dev CASCADE`). Contents:

| table | rows | notes |
|---|---|---|
| `dev.fields` | 1 | `PTZ-01`, PostGIS polygon, 1.30 ha, Petrizzo |
| `dev.stations` | 10 | `A1`..`A9` soil stations in a 3x3 grid + `GW1` gateway, PostGIS points |
| `dev.readings` | 111,125 | 15 Jun to 8 Sep 2026, **10-minute cycle**, 4 deliberate outages, uptime 99.70 % |
| `dev.flights` | 7 | drone missions 17 Jun to 6 Sep |
| `dev.detections` | 126 | adults/larvae per sector per flight, PostGIS points |
| `dev.treatments` | 2 | with recommended rain-free windows |

- The canonical DDL is in **`db/schema.sql`** (this repo). Table names are
  unqualified: the connector selects the schema with
  `SET search_path TO <schema>, public`. The same file will later be applied to
  `public`. **`public` must stay in the path** — PostGIS is installed there, so
  `ST_AsGeoJSON`/`ST_X` do not resolve with the target schema alone.
- `readings` has `UNIQUE (station_id, measured_at)`: that is the idempotency key
  the gateway relies on (`INSERT ... ON CONFLICT DO NOTHING`).

### 1.3 Networks and tunnels (from the laptop)
| Tunnel profile | Network | What it reaches |
|---|---|---|
| `pbuonocunto.conf` (NCSRD) | routes `10.0.0.0/8` | portal `10.160.101.211:5000`, DB `10.160.101.65:5432`, DNS `.5` |
| `wg-agrarian-agrarian_p2_0.conf` (DLR) | routes `10.5.6.0/24` | Testbed 2 entry VM `10.5.6.10` (SSH 22 open, ~57 ms RTT, OpenSSH 8.9 = Ubuntu 22.04); `.20` also Ubuntu 22.04 with SSH; `.1` Debian 11 with SSH (likely the gateway/router); nothing else exposed on any of them |

Both tunnels can be up at once; the DLR /24 wins by longest prefix. Verified.

**Unknown, and the whole point of section 6:** whether a container running on a
Testbed 2 node has any route to `10.160.101.65`. Nobody has answered this yet.

### 1.4 Repository, registry, CI
- Local repo: `tech/pataterno-app`. Remote `org` (default for `main`) =
  `github.com/agrarian-application-repository/pataterno-app`; remote `origin` =
  the personal copy. **Every push to `org` publishes**
  `ghcr.io/agrarian-application-repository/pataterno-app:latest` (and a
  `sha-<commit>` tag), multi-arch amd64+arm64. That registry is public.
- CI = three jobs, test -> build -> package. The **test job greps `src/` for the
  literal string `password=`** and fails the build if found. See 3.2.
- Current code: FastAPI on Python 3.10-slim, in-memory `READINGS` list, two
  hardcoded detections, mocked dashboard, 7 pytest tests. No DB code yet.
- No Docker on the development laptop; the image is built only by CI.

- **No credential ships in the repository or the image.** The AGRARIAN Database
  Guide, section 5, requires it: *"Never hardcode your database credentials
  directly into your source code. Always use Environment Variables (e.g., .env
  files) to manage secrets."* This repository lives in the AGRARIAN GitHub
  organisation and its image is public, so the rule is followed literally:
  `config/defaults.env` carries only host, port, database, user and schema, and
  the password arrives at run time from `.env`, an environment variable, or a
  file mounted at `/app/config/db.env`.
- Running **without** a credential is a supported mode (`no_credentials`), not a
  failure. It is in fact the mode the testbed experiment runs in: stage 1 of
  `/dbcheck` needs no password to prove routing.
- All writes in this increment go to schema **`dev`**, never `public`. This is
  enforced in code, not only in prose: the write endpoint refuses `public`
  unless `DB_ALLOW_PUBLIC_WRITE=1`.
- Never connect to any other database on that server (other pilots' data).

---

## 2. Architecture of this increment

```
                 laptop / gateway / testbed IoT node
                          |  HTTP
                          v
   +-------------------- pataterno-app container --------------------+
   |  FastAPI                                                       |
   |   GET  /health          (unchanged, must never touch the DB)   |
   |   GET  /dbcheck         NEW  diagnostics, read-only            |
   |   POST /dbcheck/write   NEW  inserts one marker row            |
   |   GET  /stations        NEW  from DB                           |
   |   GET  /readings/latest CHANGED: DB first, memory fallback     |
   |   POST /readings        unchanged (memory) in this increment   |
   |   GET  /detections      CHANGED: DB first, memory fallback     |
   |   GET  /dashboard       CHANGED: renders DB data when available|
   |                                                                |
   |  db.py  : config + connection + queries + degradation          |
   +----------------------------------|-----------------------------+
                                      |  psycopg, TCP 5432, timeout
                                      v
                    10.160.101.65:5432  pataterno_db  schema dev
```

---

## 3. Requirements

### 3.1 Configuration (env vars, with these defaults)
```
DB_ENABLED=1                 # "0" disables all DB access and opens no socket
DB_HOST=10.160.101.65
DB_PORT=5432
DB_NAME=pataterno_db
DB_USER=pataterno
DB_SCHEMA=dev                # switch to public when the real tables exist
DB_CONNECT_TIMEOUT=5         # libpq, seconds
DB_TCP_TIMEOUT=3             # credential-free stage-1 probe, seconds
DB_STATEMENT_TIMEOUT=10000   # ms
DB_COOLDOWN_S=30             # after a failure, skip the DB for this long
APP_PORT=80                  # listen port (use 8000 locally)
```
`DB_PASSWORD` is deliberately absent from the table: it has no default anywhere
in the repository or the image.

Precedence, lowest to highest: built-in defaults → `config/defaults.env`
(non-secret, shipped) → `.env` in the repo root (git-ignored) → environment →
`/app/config/db.env` (mounted, KEY=VALUE lines). The mounted file wins because
it is the operator's override of whatever the image was built with.

Three derived modes, because "enabled" is not binary:

| condition | mode | data endpoints | stage 1 | stages 2-3 |
|---|---|---|---|---|
| `DB_ENABLED=0` | `disabled` | memory | **skipped**, no socket | skipped |
| no credential configured | `no_credentials` | memory | **runs** | skipped |
| credential configured | `enabled` | DB-first | runs | run |

`no_credentials` is what makes the testbed probe meaningful even if the bundled
credential is later rotated away.

### 3.2 Credentials and the CI secret scan
- No credential is committed. `config/defaults.env` holds only the non-secret
  connection parameters; `config/db.env.example` is the template to copy.
- Nothing under `src/` may produce the literal text `password=` (nor `secret=`,
  `api_key`, `token=`), or the CI test job fails. Concretely: build the psycopg
  connection from a dict — `psycopg.connect(**{... "password": pw})` is fine
  because `"password":` has no `=`; `psycopg.connect(password=pw)` is **not**.
  Two consequences that are easy to miss:
  - the config field is named `db_pass`, not `password`, so no keyword call or
    `dataclasses.replace` can generate the banned token;
  - the redaction regex must be **assembled at runtime**
    (`_PW_KEY = "pass" + "word"`), because writing the pattern out literally
    would itself fail the scan.
  `tests/test_no_secrets.py` runs the same four greps locally, so CI is never
  the first place this is discovered.
- `/dbcheck` must never return the credential; log lines must never contain it.
  psycopg echoes the conninfo in some errors, so every message leaving the
  connector goes through `redact()`.
- `.gitignore` covers `.env` and `/app/config/db.env`; `config/defaults.env` is
  committed on purpose. `.dockerignore` excludes `.git`, `.venv` and `.env`
  (without it, `COPY . .` bakes all of them into a public image).

### 3.3 Degradation
- Startup must succeed even if the DB is unreachable or `DB_ENABLED=0`.
- `/health` returns `{"status":"healthy"}` regardless of DB state (the Docker
  HEALTHCHECK depends on it).
- Read endpoints try the DB once per request with the configured timeout; on
  any failure they serve the in-memory data and include
  `"source": "memory"` vs `"source": "db"` in the JSON.
- Keep one connection per request (no pool needed) but reuse a module-level
  `psycopg.Connection` if it is open and healthy; reconnect on error.

### 3.4 `GET /dbcheck` (read-only) returns
```json
{
  "checked_at": "...Z",
  "container": {"hostname": "...", "egress_ip": "10.x.x.x"},
  "target": {"host": "10.160.101.65", "port": 5432, "dbname": "pataterno_db", "user": "pataterno", "schema": "dev"},
  "tcp":  {"reachable": true, "ms": 57.3},
  "auth": {"ok": true, "server_version": "PostgreSQL 15.8 ...", "postgis": "3.4.3"},
  "schema": {"search_path": "dev", "tables": {"fields": 1, "stations": 10, "readings": 111125, "flights": 7, "detections": 126, "treatments": 2, "container_pings": 0}},
  "latest_reading_at": "2026-09-08T23:50:00Z",
  "round_trip_ms": 61.0,
  "error": null
}
```
- `egress_ip`: the local address of a UDP socket "connected" to the DB host
  (no packet sent). It tells us which network the container actually sat on.
- On failure, fill what was reached and put the exception text in `error`
  (with the password redacted if psycopg echoes conninfo).
- Same JSON printed as **one line to stdout at startup**, prefixed
  `DBCHECK `, so `docker logs` / `kubectl logs` show it even when no port is
  reachable.

### 3.5 `POST /dbcheck/write`
- Ensures table `dev.container_pings` exists:
  ```sql
  CREATE TABLE IF NOT EXISTS container_pings (
      ping_id     bigserial PRIMARY KEY,
      pinged_at   timestamptz NOT NULL DEFAULT now(),
      hostname    text,
      egress_ip   text,
      image_tag   text,
      note        text
  );
  ```
  (unqualified, so it lands in `search_path` = `dev`).
- Inserts one row and returns it (`ping_id`, `pinged_at`). Optional JSON body
  `{"note": "..."}`. `image_tag` = env `IMAGE_TAG` if set (CI can set it) else
  `"unknown"`.
- This is the proof row for the testbed test. Deliberately no foreign keys.

### 3.6 Read endpoints against `dev`
- `GET /stations`: all rows of `stations` with `ST_AsGeoJSON(geom)` as
  `geometry`, `kind`, `label`.
- `GET /readings/latest`: `SELECT DISTINCT ON (station_id) ... ORDER BY station_id, measured_at DESC`.
- `GET /detections?min_confidence=`: from `detections` joined to `flights`,
  newest flight first, `ST_AsGeoJSON(geom)` as `geometry`.
- `GET /dashboard`: when the DB is available, render the real latest readings
  and the latest flight's detections; otherwise the existing mock.

### 3.7 Tests and CI
- Existing 7 tests must pass **with `DB_ENABLED=0`** (CI has no DB).
- Add tests: `/dbcheck` with `DB_ENABLED=0` returns a well-formed document with
  `tcp.reachable == false` and no exception; `/readings/latest` falls back to
  memory and reports `"source": "memory"`.
- Add `psycopg[binary]>=3.1` to `requirements.txt`. Keep Python 3.10.
- Dockerfile: unchanged apart from the requirements layer; keep the non-root
  user, port 80, the existing HEALTHCHECK. Optionally `ENV IMAGE_TAG` set from
  a build arg in CI.

### 3.8 Documentation
- `README.md`: a short "Database" section listing the env vars, the
  `dev`/`public` switch, and the two `/dbcheck` endpoints.

---

## 4. Local verification (laptop, tunnel `pbuonocunto` up)

```bash
cd tech/pataterno-app
pip install -r requirements.txt
set DB_SCHEMA=dev
python src/app.py                      # startup log must show a DBCHECK line
curl http://localhost/dbcheck          # tcp.reachable true, tables listed
curl -X POST http://localhost/dbcheck/write -H "Content-Type: application/json" -d "{\"note\":\"laptop\"}"
curl http://localhost/readings/latest  # 9 stations, "source": "db"
curl http://localhost/stations         # 10 rows with GeoJSON
```
Then, with the tunnel **down**, restart and confirm `/health` still answers and
`/readings/latest` says `"source": "memory"`.

Verify the write from SQL (any client, same credentials):
`SELECT * FROM dev.container_pings ORDER BY ping_id DESC LIMIT 5;`

Acceptance for this section: all four curls behave as stated, pytest green
locally with `DB_ENABLED=0`, CI green after push, new image on GHCR.

---

## 5. What NOT to do
- Do not create anything in `public`.
- Do not connect to any database other than `pataterno_db`.
- Do not change `db/schema.sql` semantics (column names are the data contract
  sent to SIMAVI). Adding `container_pings` is fine; it is a diagnostics table.
- Do not put the literal `password=` in `src/`.
- Do not print or return the password anywhere.

---

## 6. Testbed procedure (needs access; two routes, either works)

**Route A, via the Agrarian Portal / k3s orchestrator.** The user emails
Mazilu (NCSRD) to assign `ghcr.io/agrarian-application-repository/pataterno-app`
to the `pbuonocunto` portal account and asks whether Testbed 2 is a selectable
target. Deploy from the portal, then read the container log for the `DBCHECK`
line and, if a port is exposed, call `/dbcheck` and `/dbcheck/write`.

**Route B, via DLR directly.** SSH to the entry VM `10.5.6.10` (login not yet
received from DLR; requested). Then on the VM or the designated satellite node:
```bash
docker pull ghcr.io/agrarian-application-repository/pataterno-app:latest
docker run --rm -p 8080:80 -e DB_SCHEMA=dev \
    ghcr.io/agrarian-application-repository/pataterno-app:latest
# in another shell on the same host:
curl -s localhost:8080/dbcheck
curl -s -X POST localhost:8080/dbcheck/write -d '{"note":"testbed2"}' -H 'Content-Type: application/json'
```
(If the node has no Docker, `docker` may be `podman` or `nerdctl`; if it is a
k3s node, `crictl`/`kubectl run` instead. Report what is there.)

**Success:** `/dbcheck` shows `tcp.reachable: true`, `auth.ok: true`,
`egress_ip` in the DLR range, and one new row in `dev.container_pings` visible
from the laptop.

**If `tcp.reachable` is false from the testbed:** that is a finding, not a
failure of the code. Record `egress_ip` and the error text, and report it. The
fallback design is a ground-side writer: the container returns classifications
over HTTP and a component on the portal side (or the gateway) performs the
insert. Do not implement the fallback until the route is confirmed absent.

---

## 7. Deliverables checklist

**Stage 1 — connector and diagnostics** (shippable and verifiable on its own;
this is the whole testbed experiment)
- [x] `src/dbconfig.py` (precedence, modes, redaction — no I/O, unit-testable)
- [x] `src/db.py` (staged probe, connection, ping write, circuit breaker)
- [x] `src/app.py`: lifespan + `DBCHECK` startup line, `/dbcheck`, `/dbcheck/write`, `APP_PORT`
- [x] `tests/`: `conftest.py`, `test_db_config.py`, `test_dbcheck.py`, `test_no_secrets.py`
- [x] `requirements.txt` (`psycopg[binary]==3.2.9`), `Dockerfile`, `ci.yml`, `.dockerignore`, `.gitignore`
- [x] `db/schema.sql`: `search_path` fix + `container_pings`; `README.md` Database section
- [ ] pytest green locally, pushed to `org`, CI green, new `latest` on GHCR
- [ ] section 4 curls pass from the laptop; `dev.container_pings` has a laptop row

**Stage 2 — DB-first reads and dashboard** (decoupled from testbed access)
- [x] `src/db.py` query layer + normalisers (DB row → API shape, ISO-8601 `Z`)
- [x] `src/app.py`: `/stations`, DB-first `/readings/latest`, `/detections`, `/dashboard`
- [x] `src/dashboard.py`: accepts real rows; synthetic-data badge
- [ ] section 6 executed once access arrives; result recorded either way

Two defects surfaced only by running stage 2 against the real database, both
now fixed and worth remembering:

1. **A psycopg connection is not safe for concurrent use.** Locking only the
   *creation* of the shared connection was not enough — the execute/fetch cycle
   has to be serialised too, or a background task collides with a request and
   the errors read as `server closed the connection unexpectedly`, which looks
   like a network fault and is not one. Two dashboard viewers would have done
   the same thing.
2. **A circuit breaker must not gate its own recovery mechanism.** The
   keepalive checked `db_available()`, which is false precisely while the
   breaker is cooling down, so the app could never heal itself.

---

## 8. Review record (18 Sep 2026)

This document was reviewed before implementation. The strategy held; the
following were corrected in place, and are listed so they are not reintroduced.

**Defects**
1. **Credentials** — §1.1 forbade committing the credential while §1.5 allowed
   bundling it in the image. Resolved by following the AGRARIAN Database Guide
   §5: nothing secret is committed or shipped, and the password is supplied at
   run time (see 1.5). The experiment is unaffected, because stage 1 of the
   check proves routing without a credential.
2. **`search_path`** — setting it to the target schema alone breaks every PostGIS
   call. Must be `<schema>, public`. The same error was in `db/schema.sql`.
3. **Staging** — the check must separate TCP from auth, so a routing failure is
   never reported as an auth failure. Added `verdict` and `tcp.failure_kind`
   (`timeout` / `refused` / `no_route` / `dns`): those are three different
   emails to three different people.
4. **No DB I/O at import time** — CI runs `from app import app` with no
   environment; the probe belongs in the lifespan handler and must never raise.
5. **The CI package job runs the image with no env**, so it now passes
   `DB_ENABLED=0`; otherwise every request there pays the connect timeout.
6. **Blocking driver in `async def`** — a synchronous psycopg call inside an
   async endpoint stalls the event loop, and with it `/health`, whose
   HEALTHCHECK times out after 3 s: a database outage would have made the
   container be declared unhealthy. DB-touching endpoints are plain `def`.
7. **No `.dockerignore`** — `COPY . .` was baking `.git` and any local `.env`
   into a public image. Fixed first; it predates this increment.

**Additions**
- Circuit breaker (`DB_COOLDOWN_S`): a dead database costs one timeout, not one
  per request — the likely testbed state.
- `inet_client_addr()` reported next to the local egress IP: the post-NAT
  address the *server* sees is the strongest evidence for the routing question.
- `container_pings` documented in `db/schema.sql`, marked explicitly as
  diagnostics and not part of the SIMAVI data contract.
- `IMAGE_TAG` is a build arg CI always sets — otherwise there is no way to tell
  which build wrote a ping row.
- Rate limit and `public`-schema interlock on the write endpoint.

**Known and deliberately deferred**
- `POST /readings` cannot write to the database: `SoilReading` carries no
  `measured_at`, which is `NOT NULL` and half of `UNIQUE(station_id,
  measured_at)`, the key the gateway's `ON CONFLICT DO NOTHING` relies on; and
  its `station-NN` pattern cannot satisfy the foreign key to ids `A1..A9`. This
  is a data-contract gap to raise with SIMAVI, not just scope.
- `/dbcheck` discloses host, database, user, schema, versions and row counts to
  anyone who can reach it. Acceptable while the network is VPN-only and the
  endpoint *is* the experiment; gate it before anything production-facing.
