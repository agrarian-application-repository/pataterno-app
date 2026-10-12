# PATATERNO local satellite testbed (OpenSAND 8.2.0)

This folder rebuilds, on a single Linux machine with Docker, the OpenSAND
satellite emulation that NCSRD documented for the AGRARIAN Open Call 2
applicants (Testbed 1 *Installation Guide v8.2.0* and *User & Configuration
Guide*, Zenodo record 17966791). The topology, IP plan, configuration files
and launch order are the guide's; only the five VirtualBox VMs become five
containers and the three host-only networks become veth links.

PATATERNO uses it to run its application across an emulated satellite link
before access to Testbed 2 (DLR) is available: the farm-side producer sits
behind the satellite terminal, the edge application behind the satellite
gateway, exactly where the guide places its two workstations.

Everything below was measured on this setup on 12 October 2026
(Windows 11 + WSL2 Ubuntu 24.04, kernel 6.6, Docker 29.8).

## 1. Topology and mapping to the NCSRD guide

```
 ws_st 192.168.2.2 ── lan_st ── st 192.168.2.1 │ 192.168.18.15 ──┐
 (farm side: producer)                (Terminal, ST1)             │
                                                                 emu ── sat 192.168.18.1
                                                                  │     (Satellite, transparent)
 ws_gw 192.168.1.2 ── lan_gw ── gw 192.168.1.1 │ 192.168.18.42 ──┘
 (edge app server, host port 18080)    (Gateway, GW0)
```

| NCSRD guide (VirtualBox) | This testbed (container) | Addresses |
|---|---|---|
| Satellite VM | `os-sat` | emu 192.168.18.1 |
| Gateway VM | `os-gw` | emu 192.168.18.42, LAN 192.168.1.1, OpenSAND bridge 192.168.63.254 |
| Terminal VM | `os-st` | emu 192.168.18.15, LAN 192.168.2.1, OpenSAND bridge 192.168.63.15 |
| Workstation Gateway (ws_gw) | `os-ws-gw` | LAN 192.168.1.2 (+ Docker bridge for Internet, port 18080 → 8080) |
| Workstation Terminal (ws_st) | `os-ws-st` | LAN 192.168.2.2 (+ Docker bridge for Internet) |
| vboxnet0 (satellite link) | segment `emu`: bridge `br0` in netns `pt-emu`, ports `emu-sat/gw/st` | 192.168.18.0/24 |
| vboxnet1 (gateway LAN) | segment `lan_gw`: veth `lan` gw ↔ ws_gw | 192.168.1.0/24 |
| vboxnet2 (terminal LAN) | segment `lan_st`: veth `lan` st ↔ ws_st | 192.168.2.0/24 |
| Adapter 1 (bridged, Internet) | Docker bridge `pataterno-testbed_egress` on ws_gw/ws_st only | 172.20.0.0/16 |

The satellite-side segments are plain veth pairs on purpose: a Docker bridge
network would apply Docker's isolation rules and drop the routed
ws_st ↔ ws_gw traffic.

## 2. Configuration used

**Software.** OpenSAND 8.2.0 (`opensand`, `opensand-core`, GSE and RLE plugins)
from the CNES net4sat repository, Ubuntu 22.04 base - guide step 4.

**Configuration files.** The CNES "IP mode" set
`opensand-network/opensand_cli/ip/{sat,gw,st,ws_gw,ws_st}` from
github.com/CNES/opensand (commit `9fff1c0`), copied **unmodified** into
`opensand_cli/ip/` (GPLv3, CNES/TAS headers kept). These are the same values
the guide's web tool (opensand-deploy) writes for its `default.xml`
templates, with one exception noted below. The guide's steps map to:

| Guide step | Here (`up.sh`) |
|---|---|
| edit `EMU_IFACE` / `LAN_IFACE` in each Makefile | passed as make variables: `EMU_IFACE=emu LAN_IFACE=lan` |
| `sudo make network` on each VM | `make network` in each container |
| deploy XML via the web interface | `make generate-xml ENABLE_COLLECTOR=false` (no ELK/InfluxDB collector) |
| launch sat → gw → st with `opensand -v -i … -t … -p …` | same commands, same order |

**Two run-time changes**, written to `profile_run.xml` (upstream files untouched):

1. `decap_buffer_len` 8 → **5000**. The CLI example ships 8; the parameter is
   the GSE decapsulator's maximum packet length in bytes, so with 8 the
   terminal drops every real packet (`Gse.cpp: error during decapsulation :
   DecapErrorSizePduBuffer`) - the link logs on but carries no traffic.
   5000 is the value opensand-deploy writes into its default profile, i.e.
   what the guide's web-UI path produces. (This is the 8.2.0 counterpart of
   the GSE error the guide's section 4C fixes for older versions.)
2. `delay_value` = `SAT_DELAY_MS` (default **125**, the upstream value).

**Link settings** (from `topology.xml` / `profile.xml`):

| Parameter | Forward link (gw → st) | Return link (st → gw) |
|---|---|---|
| Standard | DVB-S2, ACM, MODCOD 1-28 | DVB-RCS2, DAMA, waveforms 3-12 |
| Configured bandwidth | 54 000 kHz, roll-off 0.35 | 48 000 kHz, roll-off 0.2 |
| Symbol rate (bandwidth / (1 + roll-off)) | 40 Mbaud | 40 Mbaud |
| Clear-sky C/N | 20 dB, ideal attenuation 0 dB | 20 dB, ideal attenuation 0 dB |
| Terminal allocation | - | CRA 3000 kbit/s + RBDC (Legacy DAMA) |
| Encapsulation | GSE | RLE |
| Delay per space hop | 125 ms (ground → sat or sat → ground) | 125 ms |
| Frame durations | 10 ms | 26.5 ms |

So one way is 250 ms and the round trip ~500 ms, as in the guide's
verification step.

**Host settings applied by `up.sh`:** kernel modules `tun`, `sch_netem`;
`net.core.rmem_max` / `wmem_max` raised to 16 MiB (OpenSAND requests large
carrier socket buffers; the default cap of 208 KiB produced "we may have
lost UDP packets" errors); TX checksum offload disabled on every
satellite-side veth (otherwise frames crossing the OpenSAND TAP arrive with
unfinished checksums: ping works, TCP does not).

## 3. How to run it

Requirements: Linux (or WSL2) with Docker, run as root, ~1 GB RAM free.

```bash
cd testbed/opensand
./up.sh                 # build-free start (BUILD=1 ./up.sh rebuilds the image)
./verify.sh             # RTT, traceroute, iperf3 -> results/verify_*.txt / verify_latest.json
./transfer.sh           # app-sized uploads 1 KB..5 MB -> results/transfer_latest.json
./leo_passes.sh --up 600 --down 900 --cycles 4    # LEO-style visibility schedule
./gap_test.sh           # upload started during a visibility gap
./logs.sh               # OpenSAND log summary (./logs.sh tail for raw lines)
./down.sh
```

`SAT_DELAY_MS=15 ./up.sh` starts the same platform with a LEO-like
propagation delay (15 ms per hop).

**WSL2 note.** WSL terminates an idle distribution shortly after the last
`wsl.exe` session closes, which restarts Docker and destroys the testbed
(containers exit with code 143, netns `pt-emu` disappears). Keep one WSL
terminal open, or run `wsl -d Ubuntu -- sleep infinity` in a spare window,
for as long as the testbed is needed. After any Docker restart, run
`./up.sh` again (it is idempotent).

## 4. Attaching the PATATERNO containers

Application containers join a workstation's network namespace, so they see
its interfaces and routes and need no extra privileges:

```bash
# edge application (behind the satellite gateway)
docker run -d --name pt-edge --network container:os-ws-gw \
  -e ROLE=edge ghcr.io/agrarian-application-repository/pataterno-app:latest
#   -> listens on 192.168.1.2:8080 inside the testbed, http://localhost:18080 from the host

# producer (farm side, behind the satellite terminal)
docker run -d --name pt-producer --network container:os-ws-st \
  -e ROLE=producer -e EDGE_URL=http://192.168.1.2:8080 \
  ghcr.io/agrarian-application-repository/pataterno-app:latest
#   -> every request to 192.168.1.2 crosses st -> sat -> gw
```

(Inside this compose project the equivalent is `network_mode: "service:ws_gw"`,
see the commented services in `compose.yaml`.) The edge container keeps
Internet access through ws_gw's Docker bridge (weather API, image pulls).
Checked: an HTTP server in the ws_gw namespace answers on
`http://localhost:18080` from WSL and from Windows, and a client in the
ws_st namespace reaches `192.168.1.2:8080` across the satellite.

If `os-ws-gw` / `os-ws-st` is recreated, restart the attached containers.

## 5. Measured results

### 5.1 Path and round-trip time (`verify.sh`)

Traceroute confirms that traffic crosses the emulated satellite and not a
Docker shortcut:

```
ws_st -> ws_gw:  1 192.168.2.1 0.04 ms | 2 192.168.63.254 530 ms | 3 192.168.1.2 530 ms
ws_gw -> ws_st:  1 192.168.1.1 0.03 ms | 2 192.168.63.15  513 ms | 3 192.168.2.2 513 ms
```

| Ping (10 samples, 0% loss) | 125 ms/hop (NCSRD default) | 15 ms/hop (LEO-like) |
|---|---|---|
| gw → st (192.168.63.15, upstream test) | 510 / 524 / 535 ms | 66 / 79 / 94 ms |
| ws_st → ws_gw | 515 / 524 / 535 ms | 68 / 81 / 87 ms |
| ws_gw → ws_st | 515 / 526 / 538 ms | 72 / 83 / 94 ms |

(min / avg / max. The ~20-30 ms above twice the propagation delay is
OpenSAND's frame scheduling and DAMA.)

### 5.2 Throughput (`verify.sh`, iperf3, ws_st client ↔ ws_gw server)

| Test | 125 ms/hop | 15 ms/hop |
|---|---|---|
| UDP forward link, 100 Mbit/s offered | 99.8 Mbit/s, 0.2% loss | 99.6 Mbit/s, 0.4% loss |
| UDP return link, 20 Mbit/s offered | **8.65 Mbit/s** (return capacity), rest dropped at the terminal FIFO | 8.67 Mbit/s |
| TCP return link (ws_st → ws_gw), 20 s | **2.6 Mbit/s**, 0 retransmits | 2.85 Mbit/s |
| TCP forward link (ws_gw → ws_st), 20 s | 4.2 - 10.8 Mbit/s over four runs, 39-687 retransmits | 83 Mbit/s |

The farm → edge direction is the DVB-RCS2 return link: a single TCP flow
settles near the terminal's 3000 kbit/s constant-rate allocation. Forward
TCP at 500 ms RTT is the classic GEO case without a performance-enhancing
proxy (slow start overshoot, retransmissions) and varies run to run.

### 5.3 Application-sized uploads (`transfer.sh`, new TCP connection each, 3 samples)

| Payload (ws_st → ws_gw) | 125 ms/hop, median | 15 ms/hop, median |
|---|---|---|
| 1 KB (alert) | 1.06 s | 0.16 s |
| 10 KB | 1.08 s | 0.18 s |
| 150 KB (detection package) | 2.78 s | 0.61 s |
| 1 MB | 5.36 s | 2.97 s |
| 5 MB | 16.5 s | 14.1 s |

Min-max spread was under 0.05 s for every size.

### 5.4 Visibility gaps (`leo_passes.sh`, `gap_test.sh`)

`leo_passes.sh` alternates passes and gaps by applying netem `loss 100%` on
both directions of the terminal's `emu` interface (uplink on `os-st`,
downlink on the `pt-emu` bridge port). Observed with a continuous ping
(15 s passes, 30 s gaps, 125 ms/hop):

- OpenSAND keeps running through the gap and the terminal stays logged on;
  traffic resumes **4-5 s after each pass starts** (DVB resynchronisation).
- Packets sent during a gap are **not lost**: without the gateway's
  frame synchronisation the terminal gets no return-link allocation and
  holds them in its DVB FIFO (best-effort capacity 6000 packets), then
  releases them at the next pass (pings sent through a 30 s gap all
  arrived together, 34 s → 5.6 s late).
- `gap_test.sh`: a 150 KB upload started 2 s into a 30 s gap connected
  28.9 s later (as the pass began) and was delivered **3.6 s after the
  pass start** (2.8 s on a continuous link). The TCP connection survives
  the gap.

The schedule is an approximation (fixed pass and gap lengths given as
inputs), not orbital mechanics; it is meant to be replaced by the pass
parameters of the target testbed. All transitions are logged with epoch
time to `results/leo_passes.log` so application timestamps can be matched
to link state.

## 6. Logs

`./logs.sh` summarises `/var/log/opensand.log` of each entity. Expected and
harmless: `dvb.qosserver ... TCP is not available` every 5 s on st (no QoS
server in this setup); `dvb.scheduling` multicast errors on gw during the
first second, before the terminal logs on; FIFO-full / RLE reassembly
errors only while a test deliberately overloads the return link (UDP at
20 Mbit/s); "we may have lost UDP packets" right after a gap.

## 7. Porting to the AGRARIAN testbeds

**Testbed 1 (NCSRD, OpenSAND on VMs).** Same software, same files, same IP
plan: the containers are the guide's VMs. Moving there means running the
two PATATERNO containers on the ws_gw and ws_st VMs with
`docker run --network host ...` (the VMs already carry the routes), with
`EDGE_URL=http://192.168.1.2:8080` for the producer. If those VMs are
configured with the CLI Makefiles rather than the web tool, the
`decap_buffer_len` change of section 2 applies there too.

**Testbed 2 (DLR, LEO constellation).** The emulator software is not named
in the AGRARIAN documents. The application side does not depend on
OpenSAND: the producer needs an IP route to the edge application, the
edge application an outbound route to the AGRARIAN data storage. What has
to come from DLR is the attachment point (where the producer runs - an
emulated IoT node or an external data-producer server - and where the edge
application runs - on board or at the ground station), the container
runtime there, and the constellation's pass and delay parameters, which
replace `SAT_DELAY_MS` and the `leo_passes.sh` schedule.

## 8. Files

| File | Purpose |
|---|---|
| `Dockerfile` | one node image: Ubuntu 22.04 + OpenSAND 8.2.0 + network tools + CNES configs |
| `compose.yaml` | the five nodes (`os-sat`, `os-gw`, `os-st`, `os-ws-gw`, `os-ws-st`) |
| `opensand_cli/ip/` | CNES IP-mode Makefiles and XML, unmodified |
| `up.sh` / `down.sh` | wire, configure, launch / tear down |
| `verify.sh` | RTT, traceroute, iperf3 |
| `transfer.sh`, `transfer_test.py` | application-sized upload timing |
| `leo_passes.sh` | pass/gap schedule with netem |
| `gap_test.sh` | upload during a gap |
| `logs.sh` | OpenSAND log summary |
| `results/` | measured outputs (`*_geo125ms.json`, `*_leo15ms.json`, gap test, pass log) |
