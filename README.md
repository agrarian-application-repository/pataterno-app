# pataterno-demo-app - AGRARIAN Project

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

## Run locally

```bash
pip install -r requirements.txt
python src/app.py            # serves on :80
python -m pytest tests/ -v   # run the test suite
```

## Run the container

```bash
docker run -p 8080:80 ghcr.io/<owner>/pataterno-demo-app:latest
curl http://localhost:8080/health
```
