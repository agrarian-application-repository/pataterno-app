#!/usr/bin/env bash
# verify.sh - check and measure the satellite link of the local testbed.
#
#  1. ping RTT  gw -> st (192.168.63.15, the upstream test), ws_st -> ws_gw, ws_gw -> ws_st
#  2. traceroute ws_st -> ws_gw and back: proves traffic crosses st/gw (the satellite)
#  3. iperf3 TCP and UDP, uplink (ws_st -> ws_gw = return link, DVB-RCS2)
#     and downlink (ws_gw -> ws_st = forward link, DVB-S2)
#
# Results: results/verify_<timestamp>.txt and results/verify_latest.json
# Env: IPERF_T (TCP seconds, default 20), UDP_T (default 10),
#      UDP_UP_RATE (default 20M), UDP_DOWN_RATE (default 100M)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
mkdir -p results

IPERF_T="${IPERF_T:-20}"
UDP_T="${UDP_T:-10}"
UDP_UP_RATE="${UDP_UP_RATE:-20M}"
UDP_DOWN_RATE="${UDP_DOWN_RATE:-100M}"
TS="$(date +%Y%m%dT%H%M%S)"
OUT="results/verify_${TS}.txt"
JSON="results/verify_latest.json"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"; docker exec os-ws-gw pkill -x iperf3 2>/dev/null || true' EXIT

say() { echo "$*" | tee -a "$OUT"; }

say "# PATATERNO local OpenSAND testbed - verification $(date -Is)"
say "# delay per hop: $(docker exec os-gw grep -o '<delay_value>[0-9]*' /opt/opensand_cli/ip/gw/profile_run.xml | cut -d'>' -f2) ms"
say ""

ping_rtt() {   # container target label
  local out; out="$(docker exec "$1" ping -c 10 -i 0.5 -W 3 "$2" 2>&1 || true)"
  echo "$out" > "$TMP/ping_$3.txt"
  local loss rtt
  loss="$(echo "$out" | grep -o '[0-9.]*% packet loss' || echo 'n/a')"
  rtt="$(echo "$out" | grep -o 'rtt.*' || echo 'rtt n/a')"
  say "ping $3: $loss, $rtt"
}
say "## 1. Round-trip time"
ping_rtt os-gw    192.168.63.15 "gw->st(192.168.63.15)"
ping_rtt os-ws-st 192.168.1.2   "ws_st->ws_gw(192.168.1.2)"
ping_rtt os-ws-gw 192.168.2.2   "ws_gw->ws_st(192.168.2.2)"
say ""

say "## 2. Path (must go through st 192.168.2.1 / gw 192.168.63.254 or 192.168.1.1)"
say "ws_st -> ws_gw:"
docker exec os-ws-st traceroute -n -q 1 -w 3 192.168.1.2 2>&1 | tee -a "$OUT"
say "ws_gw -> ws_st:"
docker exec os-ws-gw traceroute -n -q 1 -w 3 192.168.2.2 2>&1 | tee -a "$OUT"
say ""

say "## 3. Throughput (iperf3 server on ws_gw, client on ws_st)"
docker exec os-ws-gw pkill -x iperf3 2>/dev/null || true
docker exec -d os-ws-gw iperf3 -s
sleep 1
run_iperf() {  # name args...
  local name="$1"; shift
  docker exec os-ws-st iperf3 -c 192.168.1.2 -J "$@" > "$TMP/$name.json" 2>&1 || true
}
run_iperf tcp_up   -t "$IPERF_T"
run_iperf tcp_down -t "$IPERF_T" -R
run_iperf udp_up   -u -b "$UDP_UP_RATE"   -t "$UDP_T" -l 1200
run_iperf udp_down -u -b "$UDP_DOWN_RATE" -t "$UDP_T" -l 1200 -R

python3 - "$TMP" "$JSON" "$TS" "$UDP_UP_RATE" "$UDP_DOWN_RATE" <<'PY' | tee -a "$OUT"
import json, sys, re, pathlib
tmp, out_json, ts = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
offered_rate = {"udp_up": sys.argv[4], "udp_down": sys.argv[5]}
def rate_mbps(s):
    m = re.fullmatch(r"([\d.]+)([KMG]?)", s.strip(), re.I)
    return float(m[1]) * {"": 1e-6, "K": 1e-3, "M": 1, "G": 1e3}[m[2].upper()]
res = {"timestamp": ts, "ping": {}, "iperf3": {}}
for p in tmp.glob("ping_*.txt"):
    m = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", p.read_text())
    l = re.search(r"([\d.]+)% packet loss", p.read_text())
    res["ping"][p.stem[5:]] = {"min_ms": float(m[1]), "avg_ms": float(m[2]), "max_ms": float(m[3]),
                               "loss_pct": float(l[1]) if l else None} if m else {"error": "no reply"}
labels = {"tcp_up": "TCP uplink   (ws_st->ws_gw, return link)",
          "tcp_down": "TCP downlink (ws_gw->ws_st, forward link)",
          "udp_up": "UDP uplink   (offered %s)", "udp_down": "UDP downlink (offered %s)"}
for name in ["tcp_up", "tcp_down", "udp_up", "udp_down"]:
    f = tmp / f"{name}.json"
    try:
        raw = f.read_text()
        d = json.loads(raw[raw.index("{"):])   # iperf3 may print a WARNING line first
    except Exception:
        print(f"{name}: no result ({f.read_text()[:200]!r})"); res["iperf3"][name] = {"error": "no result"}; continue
    if "error" in d:
        print(f"{name}: iperf3 error: {d['error']}"); res["iperf3"][name] = {"error": d["error"]}; continue
    e = d["end"]
    if name.startswith("tcp"):
        mbps = e["sum_received"]["bits_per_second"] / 1e6
        r = {"received_mbps": round(mbps, 2), "retransmits": e["sum_sent"].get("retransmits")}
        print(f"{labels[name]}: {mbps:.2f} Mbit/s received, retransmits {r['retransmits']}")
    else:
        s = e["sum"]
        offered = rate_mbps(offered_rate[name])
        recv_mbps = s["bits_per_second"] * (1 - s["lost_percent"] / 100) / 1e6
        r = {"offered_mbps": round(offered, 2), "delivered_mbps": round(recv_mbps, 2),
             "lost_pct": round(s["lost_percent"], 2), "jitter_ms": round(s["jitter_ms"], 2)}
        print(f"{labels[name] % offered_rate[name]}: {recv_mbps:.2f} Mbit/s delivered, "
              f"loss {s['lost_percent']:.1f}%, jitter {s['jitter_ms']:.1f} ms")
    res["iperf3"][name] = r
pathlib.Path(out_json).write_text(json.dumps(res, indent=2))
PY
say ""
say "saved: $OUT and $JSON"
