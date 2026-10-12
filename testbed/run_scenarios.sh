#!/usr/bin/env bash
# run_scenarios.sh - PATATERNO across the local OpenSAND testbed.
#
# Attaches the two PATATERNO roles to the testbed workstations (edge behind
# the satellite gateway, producer behind the satellite terminal), sends the
# drone frames across the emulated satellite, and records the producer's
# KPI-6 / KPI-2 report plus the IP bytes that actually entered the satellite
# terminal.
#
#   A  continuous link
#   B  LEO-style passes (time-compressed): the link starts in a gap, then
#      alternates pass/gap via opensand/leo_passes.sh while frames keep coming
#
# Usage (root, testbed up via opensand/up.sh):
#   ./run_scenarios.sh [image]       default: ghcr.io/agrarian-application-repository/pataterno-app:latest
# Env: PASS_UP (s, default 20), PASS_DOWN (s, default 40), FRAMES_B_REPEAT (default 3)
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${1:-ghcr.io/agrarian-application-repository/pataterno-app:latest}"
FRAMES="$HERE/frames"
OUT="$HERE/results"
mkdir -p "$OUT"
PASS_UP="${PASS_UP:-20}"; PASS_DOWN="${PASS_DOWN:-40}"; REPEAT_B="${FRAMES_B_REPEAT:-3}"
EDGE_URL="http://192.168.1.2:8080"

for c in os-sat os-gw os-st os-ws-gw os-ws-st; do
  docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null | grep -q true || { echo "testbed not up ($c)"; exit 1; }
done
docker pull -q "$IMAGE" >/dev/null 2>&1 || echo "note: could not pull $IMAGE, using the local copy"

# ws_st's interface towards the satellite terminal (the one holding 192.168.2.2)
ST_IF=$(docker exec os-ws-st sh -c "ip -o -4 addr show | awk '/192\.168\.2\.2\//{print \$2}'" | head -1)
echo "producer side interface: $ST_IF"
txb() { docker exec os-ws-st cat "/sys/class/net/$ST_IF/statistics/tx_bytes"; }
rxb() { docker exec os-ws-st cat "/sys/class/net/$ST_IF/statistics/rx_bytes"; }

start_edge() {
  docker rm -f pt-edge >/dev/null 2>&1
  docker run -d --name pt-edge --network container:os-ws-gw \
    -e APP_PORT=8080 -e DB_ENABLED=0 -e PHONE_HOME_URL= \
    --health-cmd "curl -fs http://localhost:8080/health || exit 1" \
    "$IMAGE" >/dev/null
  for i in $(seq 1 30); do
    docker exec pt-edge curl -fs http://localhost:8080/health >/dev/null 2>&1 && break; sleep 1
  done
  docker exec pt-edge curl -fs "http://localhost:8080/model" | head -c 160; echo
}

run_producer() { # $1 name, extra env...
  local name="$1"; shift
  docker run --rm --name "pt-producer-$name" --network container:os-ws-st \
    -e ROLE=producer -e EDGE_URL="$EDGE_URL" -e FRAMES_DIR=/frames \
    -e RESULTS_PATH="/out/scenario_$name.json" -e HTTP_TIMEOUT_S=10 -e RETRY_DELAY_S=2 \
    "$@" -v "$FRAMES":/frames:ro -v "$OUT":/out "$IMAGE"
}

echo "== reachability across the satellite"
docker exec os-ws-st sh -c "curl -s -o /dev/null -w 'edge /health over the satellite: %{http_code} in %{time_total}s\n' $EDGE_URL/health" 2>/dev/null || true

echo "== Scenario A: continuous link"
"$HERE/opensand/leo_passes.sh" --clear >/dev/null 2>&1 || true
start_edge
docker exec os-ws-st sh -c "curl -s -o /dev/null -w 'edge /health over the satellite: %{http_code} in %{time_total}s\n' $EDGE_URL/health"
TX0=$(txb); RX0=$(rxb)
run_producer A -e SEND_PREVIEW=1
TX1=$(txb); RX1=$(rxb)
echo "{\"scenario\":\"A\",\"ip_bytes_into_satellite\":$((TX1-TX0)),\"ip_bytes_back\":$((RX1-RX0))}" > "$OUT/link_A.json"
cat "$OUT/link_A.json"
docker exec pt-edge curl -fs "http://localhost:8080/alerts?limit=5" > "$OUT/alerts_A.json"

echo "== Scenario B: LEO passes ${PASS_UP}s up / ${PASS_DOWN}s down, starting in a gap"
start_edge
TX0=$(txb); RX0=$(rxb)
"$HERE/opensand/leo_passes.sh" --start down --up "$PASS_UP" --down "$PASS_DOWN" --cycles 3 > "$OUT/passes_B.log" 2>&1 &
PASSES=$!
sleep 1
run_producer B -e SEND_PREVIEW=1 -e REPEAT="$REPEAT_B" -e FRAME_INTERVAL_S=5 -e RUN_SECONDS=400
kill "$PASSES" 2>/dev/null; wait "$PASSES" 2>/dev/null
"$HERE/opensand/leo_passes.sh" --clear >/dev/null 2>&1 || true
TX1=$(txb); RX1=$(rxb)
echo "{\"scenario\":\"B\",\"pass_up_s\":$PASS_UP,\"pass_down_s\":$PASS_DOWN,\"ip_bytes_into_satellite\":$((TX1-TX0)),\"ip_bytes_back\":$((RX1-RX0))}" > "$OUT/link_B.json"
cat "$OUT/link_B.json"
cp "$HERE/opensand/results/leo_passes.log" "$OUT/leo_passes_B.log" 2>/dev/null || true
docker exec pt-edge curl -fs "http://localhost:8080/alerts?limit=20" > "$OUT/alerts_B.json"

echo "== edge left running for the dashboard: http://localhost:18080/dashboard"
