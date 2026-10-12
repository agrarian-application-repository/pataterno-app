# Porting PATATERNO from the local testbed to the AGRARIAN testbeds

This note is for the testbed owners (DLR for Testbed 2, NCSRD for Testbed 1)
and for us. It says what PATATERNO runs, where each piece sits on our local
satellite testbed, where it would sit on the real testbeds, and what we need
from each owner to make the move.

## 1. What runs

One Docker image, `ghcr.io/agrarian-application-repository/pataterno-app`
(amd64 and arm64, built by the AGRARIAN CI pipeline), in two roles.

| Role | Started with | What it does | Talks to |
|---|---|---|---|
| **edge** (default) | `docker run ... pataterno-app` | API on port 80: INT8 CPB classifier behind `/images`, weather window, dashboard, `/dbcheck` | receives from the producer; writes detections to `pataterno_db` when reachable; Open-Meteo over the internet for the forecast |
| **producer** | `-e ROLE=producer` | farm-side data producer: reduces each drone frame to candidate crops and sends P0 summary / P1 crops / P2 preview to the edge, highest priority first, holding them while the link is down | the edge, across the satellite link |

Footprint of the edge role, measured on our machine with the container
limited to 1 CPU: 79 MiB RAM idle, 153 MiB after classifying ten 1080p
frames; a whole 1080p frame (candidate search + crops + INT8 classification)
in 0.44 s on average. Image size 455 MB (most of it the OpenVINO runtime).
Message sizes per frame on the 17 June frames: P0 about 0.2 kB, P1 0-102 kB
(depends on the number of candidates), P2 about 57-96 kB.

## 2. Local testbed: NCSRD's OpenSAND layout in Docker

The emulator documented for the Open Call testbeds is OpenSAND (CNES), in
NCSRD's "OpenSAND Detailed Installation Guide (v8.2.0)" and "User &
Configuration Guide" (Zenodo record 17966791): Satellite, Gateway and
Terminal VMs plus two optional workstations, on VirtualBox host-only
networks. We rebuilt that layout one-to-one with Docker on a single Linux
host - each VM became a container, each `vboxnet` a Docker network, with the
same IP plan, the same OpenSAND 8.2.0 packages and the same launch order.
Details and the exact commands are in `testbed/opensand/README.md`.

| NCSRD guide (VM) | Local container | Address | PATATERNO role |
|---|---|---|---|
| Satellite (`vboxnet0`) | `os-sat` | 192.168.18.1 (emu) | - |
| Gateway | `os-gw` | 192.168.18.42 (emu), 192.168.1.1 (LAN) | - |
| Terminal | `os-st` | 192.168.18.15 (emu), 192.168.2.1 (LAN) | - |
| Workstation Gateway (`vboxnet1`) | `os-ws-gw` | 192.168.1.2 | **edge** (`ROLE=edge`, `APP_PORT=8080`, host port 18080) |
| Workstation Terminal (`vboxnet2`) | `os-ws-st` | 192.168.2.2 | **producer** (`ROLE=producer`, `EDGE_URL=http://192.168.1.2:8080`) |

Traffic from the producer to the edge crosses the emulated satellite link
(ws_st -> st -> sat -> gw -> ws_gw). The two PATATERNO containers join the
workstations' network namespaces, so they need no extra privileges.

Measured on this testbed (12-13 October 2026, `testbed/opensand/README.md`
and `testbed/results/`):

| | 125 ms per space hop (guide default) | 15 ms per hop (LEO-like delay) |
|---|---|---|
| Round trip ws_st <-> ws_gw | 522 ms | 81 ms |
| TCP farm -> edge (DVB-RCS2 return link) | 2.6 Mbit/s | 2.85 Mbit/s |
| TCP edge -> farm (DVB-S2 forward link) | 4.2-10.8 Mbit/s | 83 Mbit/s |
| 3 drone frames, continuous link: detection at the edge (p50), one reused connection | 4.3 s | 0.98 s |
| same, one TCP connection per message (v1.2.0 producer) | 7.4 s | 1.34 s |
| 3 frames, link starting in a gap of 10 / 20 / 40 / 80 s: worst detection | 13.3 / 28.6 / 43.3 / 83.1 s | 9.6 / 19.6 / 39.6 / 82.1 s |
| 9 frames, link starting in a 40 s gap, 20 s passes: worst detection | 49.5 s | 42.3 s |
| Bytes: raw frames -> IP bytes into the satellite terminal | 18.35 MB -> 392.7 kB (-97.86%) | same |

With intermittent visibility every message was delivered (27 of 27), in
priority order: all summaries, then all crops, then all previews. KPI-2 counts
the detection (the classified crops), not the summary; times start when the
frame enters the producer, so the drone-to-producer time is not included.
`testbed/run_scenarios.sh` (continuous link and 40 s gaps) and `testbed/gap_sweep.sh`
(gap length sweep) reproduce these runs with the published image; results are in
`testbed/results/{geo125,leo15}_v13*` and `testbed/results/sweep_*`. The detection
arrives 1-9 s after the gap ends (the frames enter about 1 s after it starts);
the larger values come from TCP's retry back-off after
the link returns.

LEO behaviour on top of OpenSAND: OpenSAND emulates one transparent satellite
with a fixed delay. To reproduce the intermittent visibility of a LEO
constellation we cut the terminal's satellite interface on a pass schedule
(link up for P seconds, down for G seconds) with `tc netem`. This is our
approximation; the real pass pattern comes from Testbed 2.

## 3. Moving to Testbed 1 (NCSRD)

Because the local testbed copies NCSRD's guide, the move is direct: on the
Workstation Gateway run the edge role, on the Workstation Terminal run the
producer pointing at `http://192.168.1.2:80`.

```bash
# Workstation Gateway (192.168.1.2)
docker run -d --name pataterno-edge -p 80:80 -e PHONE_HOME_URL= \
  ghcr.io/agrarian-application-repository/pataterno-app:latest

# Workstation Terminal (192.168.2.2) - frames in ./frames, report in ./frames/results.json
docker run --rm -e ROLE=producer -e EDGE_URL=http://192.168.1.2:80 \
  -e FRAMES_DIR=/frames -e RESULTS_PATH=/frames/results.json \
  -v $PWD/frames:/frames ghcr.io/agrarian-application-repository/pataterno-app:latest
```

## 4. Moving to Testbed 2 (DLR)

The Technical Annex describes Testbed 2 as a small emulated LEO constellation
(one ground station, a configurable number of satellites and of IoT nodes)
with edge computing on board, where real data enters "by integrating a
dedicated data producer server, whose outputs are then forwarded by the IoT
devices over the emulated link". The mapping is therefore:

| PATATERNO piece | Local testbed | Testbed 2 element |
|---|---|---|
| producer (`ROLE=producer`) | `ws_st` behind the satellite terminal | the **data producer server** attached to an emulated IoT node |
| satellite link | OpenSAND st - sat - gw + netem pass schedule | the emulated constellation (DLR's pass dynamics, inter-satellite links) |
| edge (`ROLE=edge`) | `ws_gw` behind the satellite gateway | the **on-board edge** of a satellite node (or the ground station, if on-board execution is modelled rather than executed) |
| detections -> `pataterno_db` | memory / `dev` schema over the VPN | AGRARIAN Data Storage, from the ground station |

Two cases, depending on how DLR's edge works:

- **The edge node executes containers.** Run the edge role there and the
  producer next to the IoT node, exactly as in section 3, with `EDGE_URL` set
  to the edge node's address.
- **The edge is a service model** ("edge computing part is based on service
  modeling" - Applicants' Guide). Then the resource and traffic profile of
  section 1 is what DLR's model needs: per frame, P0 ~0.2 kB first, P1 up to
  ~100 kB, P2 ~60-100 kB last; about 0.44 s of one CPU and ~150 MiB RAM per
  frame at the edge. The producer still runs as the data producer server, and
  its JSON report gives the delivery times over DLR's link.

### What we need from DLR

1. Access: a login for the entry VM (10.5.6.10), or the agreed way to start
   our two containers on the producer host and the edge node.
2. The constellation parameters for our run: number of satellites, orbit,
   pass duration and gap over the Petrizzo site (40.33 N, 15.61 E), link rate
   and delay, inter-satellite link latency.
3. The edge node: container runtime (Docker, k3s), CPU architecture, CPU and
   RAM available, outbound route to the AGRARIAN Data Storage.
4. How data enters the IoT node: HTTP from the producer server (what we do
   locally), or another interface.

## 5. Configuration reference

| Variable | Role | Default | Meaning |
|---|---|---|---|
| `ROLE` | both | `edge` | `edge` or `producer` |
| `EDGE_URL` | producer | `http://192.168.1.2:80` | where the edge listens |
| `FRAMES_DIR`, `FRAMES_MANIFEST` | producer | `/frames`, `<dir>/frames.json` | frames, and per-frame lat/lon/time/flight |
| `FRAME_INTERVAL_S`, `REPEAT` | producer | `0`, `1` | capture cadence, replays |
| `SEND_PREVIEW` | producer | `1` | send P2 previews |
| `RETRY_DELAY_S`, `HTTP_TIMEOUT_S`, `RUN_SECONDS` | producer | `2`, `30`, `3600` | store-and-forward timing |
| `RESULTS_PATH` | producer | stdout | KPI-6 / KPI-2 report (JSON) |
| `ROI_*` | both | see `src/vision.py` | candidate-region thresholds, crop size |
| `CPB_THRESHOLD` | edge | `0.5` | p(CPB) above which a crop is a detection |
| `WEATHER_*` | edge | see `src/weather.py` | treatment-window rules |
| `DB_ENABLED`, `DB_*` | edge | see README, Database | AGRARIAN PostgreSQL connector |
| `PHONE_HOME_URL` | edge | baked-in diagnostic listener | set empty to disable the startup diagnostic beacon |

## 6. What the report measures

The producer's JSON report (`RESULTS_PATH`) has, per frame: the raw frame
bytes, the bytes of each message as sent (KPI-6), and the times from the frame
entering the producer to the summary reaching the edge, to the crops being
classified at the edge (the detection, which KPI-2 counts), and to the result
being back at the producer. All times
are taken on one clock (the producer's, plus the edge's classification
timestamp when both run on hosts with synchronised clocks).
