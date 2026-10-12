#!/usr/bin/env bash
# gap_sweep.sh - detection time vs visibility-gap length.
#
# For each gap G: the satellite terminal's link starts DOWN for G seconds,
# then a pass of PASS_UP seconds follows. The three frames enter the producer
# at t=0, i.e. at the start of the gap - the worst moment. The producer's
# report gives, per frame, the time from frame in to the detection at the edge.
#
# Usage (root, testbed up, edge image as in run_scenarios.sh):
#   ./gap_sweep.sh [image]        env: GAPS ("0 10 20 40 80"), PASS_UP (30), OUT_TAG (sweep)
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${1:-ghcr.io/agrarian-application-repository/pataterno-app:latest}"
GAPS="${GAPS:-0 10 20 40 80}"; PASS_UP="${PASS_UP:-30}"
OUT="$HERE/results/${OUT_TAG:-sweep}"; mkdir -p "$OUT"
EDGE_URL="http://192.168.1.2:8080"
docker pull -q "$IMAGE" >/dev/null 2>&1 || true

docker rm -f pt-edge >/dev/null 2>&1
docker run -d --name pt-edge --network container:os-ws-gw -e APP_PORT=8080 -e DB_ENABLED=0 -e PHONE_HOME_URL= \
  -e DASHBOARD_WEATHER=0 "$IMAGE" >/dev/null
for i in $(seq 1 30); do docker exec pt-edge curl -fs http://localhost:8080/health >/dev/null 2>&1 && break; sleep 1; done

for G in $GAPS; do
  "$HERE/opensand/leo_passes.sh" --clear >/dev/null 2>&1 || true
  if [ "$G" -gt 0 ]; then
    "$HERE/opensand/leo_passes.sh" --start down --down "$G" --up "$PASS_UP" --cycles 1 > "$OUT/passes_gap$G.log" 2>&1 &
    PASSES=$!
    sleep 1
  fi
  docker run --rm --network container:os-ws-st -e ROLE=producer -e EDGE_URL="$EDGE_URL" -e FRAMES_DIR=/frames \
    -e RESULTS_PATH="/out/gap_$G.json" -e HTTP_TIMEOUT_S=10 -e RETRY_DELAY_S=1 -e SEND_PREVIEW=1 \
    -e RUN_SECONDS=$((G + PASS_UP + 120)) \
    -v "$HERE/frames":/frames:ro -v "$OUT":/out "$IMAGE" 2>&1 | grep "done in"
  if [ "$G" -gt 0 ]; then wait "$PASSES" 2>/dev/null; fi
  "$HERE/opensand/leo_passes.sh" --clear >/dev/null 2>&1 || true
done
echo "results in $OUT"
