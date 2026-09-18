-- ---------------------------------------------------------------------------
-- PATATERNO - schema written by the container into the AGRARIAN PostgreSQL
-- (pataterno_db). Covers the four kinds of data in the data contract sent to
-- SIMAVI: stations and field geometry, soil readings, pest detections per
-- drone flight, and treatment records with the weather-window recommendation.
--
-- Tables are unqualified: the connector sets the target schema with
--     SET search_path TO <schema>, public;
-- so the same file serves `public` (production, written by the gateway and the
-- container) and `dev` (synthetic sandbox used while the gateway is offline).
-- `public` must stay in the path: PostGIS is installed there, so ST_AsGeoJSON,
-- ST_X and friends do not resolve without it.
--
-- Geometry uses native PostGIS in SRID 4326. PostGIS 3.4.3 is installed on the
-- AGRARIAN server, so no lat/lon fallback is needed.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS fields (
    field_id     text PRIMARY KEY,
    name         text NOT NULL,
    crop         text NOT NULL,
    variety      text,
    area_ha      numeric(6,2),
    geom         geometry(Polygon,4326) NOT NULL,
    data_source  text NOT NULL DEFAULT 'gateway'
);

CREATE TABLE IF NOT EXISTS stations (
    station_id   text PRIMARY KEY,
    field_id     text REFERENCES fields(field_id),
    kind         text NOT NULL CHECK (kind IN ('soil_station','gateway')),
    label        text,
    geom         geometry(Point,4326) NOT NULL,
    installed_at timestamptz NOT NULL,
    data_source  text NOT NULL DEFAULT 'gateway'
);

-- One row per station per duty cycle. The field cycle is 10 minutes.
-- UNIQUE(station_id, measured_at) is the idempotency key: the gateway outbox
-- replays with INSERT ... ON CONFLICT DO NOTHING, so a reading is stored once.
CREATE TABLE IF NOT EXISTS readings (
    reading_id       bigserial PRIMARY KEY,
    station_id       text NOT NULL REFERENCES stations(station_id),
    measured_at      timestamptz NOT NULL,
    moisture_pct     real,
    temperature_c    real,
    ec_us_cm         real,
    ph               real,
    nitrogen_mg_kg   real,
    phosphorus_mg_kg real,
    potassium_mg_kg  real,
    battery_v        real,
    rssi_dbm         smallint,
    snr_db           real,
    data_source      text NOT NULL DEFAULT 'gateway',
    UNIQUE (station_id, measured_at)
);
CREATE INDEX IF NOT EXISTS readings_measured_at_idx  ON readings (measured_at DESC);
CREATE INDEX IF NOT EXISTS readings_station_time_idx ON readings (station_id, measured_at DESC);

CREATE TABLE IF NOT EXISTS flights (
    flight_id       text PRIMARY KEY,
    field_id        text REFERENCES fields(field_id),
    flown_at        timestamptz NOT NULL,
    waypoints       int,
    images_captured int,
    altitude_m      real,
    data_source     text NOT NULL DEFAULT 'gateway'
);

CREATE TABLE IF NOT EXISTS detections (
    detection_id   bigserial PRIMARY KEY,
    flight_id      text NOT NULL REFERENCES flights(flight_id),
    captured_at    timestamptz NOT NULL,
    geom           geometry(Point,4326) NOT NULL,
    sector         text,
    class_label    text NOT NULL DEFAULT 'colorado_potato_beetle',
    life_stage     text CHECK (life_stage IN ('adult','larva')),
    count_n        int,
    density_per_m2 real,
    confidence     real CHECK (confidence BETWEEN 0 AND 1),
    image_ref      text,
    data_source    text NOT NULL DEFAULT 'classifier'
);
CREATE INDEX IF NOT EXISTS detections_flight_idx ON detections (flight_id);
CREATE INDEX IF NOT EXISTS detections_geom_idx   ON detections USING GIST (geom);

CREATE TABLE IF NOT EXISTS treatments (
    treatment_id   bigserial PRIMARY KEY,
    field_id       text REFERENCES fields(field_id),
    recommended_at timestamptz,
    window_start   timestamptz,
    window_end     timestamptz,
    rationale      text,
    applied_at     timestamptz,
    product        text,
    dose_kg_ha     real,
    method         text,
    data_source    text NOT NULL DEFAULT 'weather_window_job'
);

-- ---------------------------------------------------------------------------
-- Diagnostics - NOT part of the data contract sent to SIMAVI.
--
-- One row per successful write from a running container. It is how we prove
-- that a given deployment (laptop, portal, Testbed 2 node) can actually reach
-- this database: `egress_ip` records which network the container sat on and
-- `image_tag` which build produced the row. Deliberately no foreign keys, so
-- the write cannot fail for reasons unrelated to connectivity.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS container_pings (
    ping_id   bigserial PRIMARY KEY,
    pinged_at timestamptz NOT NULL DEFAULT now(),
    hostname  text,
    egress_ip text,
    image_tag text,
    note      text
);
