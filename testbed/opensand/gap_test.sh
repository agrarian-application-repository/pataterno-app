#!/usr/bin/env bash
# gap_test.sh - what happens to an upload that starts while no satellite is
# visible: open a gap of GAP seconds, start a SIZE-byte upload ws_st -> ws_gw
# START_AFTER seconds into it, then restore the link and time the delivery.
# Output: results/gap_test_<timestamp>.json
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
mkdir -p results
GAP="${GAP:-30}"
START_AFTER="${START_AFTER:-2}"
SIZE="${SIZE:-150000}"
PORT=5599
TS="$(date +%Y%m%dT%H%M%S)"
OUT="results/gap_test_${TS}.json"

docker cp transfer_test.py os-ws-gw:/tmp/transfer_test.py >/dev/null
docker exec os-ws-gw pkill -f "transfer_test.py serve" 2>/dev/null || true
docker exec -d os-ws-gw python3 /tmp/transfer_test.py serve "$PORT"
sleep 1

./leo_passes.sh --start down --down "$GAP" --up 1 --cycles 1 > "results/gap_test_${TS}_schedule.log" 2>&1 &
SCHED=$!
sleep "$START_AFTER"
T0=$(date +%s.%N)
docker exec os-ws-st python3 -c "
import socket, time, json
t0 = time.monotonic()
s = socket.create_connection(('192.168.1.2', $PORT), timeout=300)
t1 = time.monotonic()
s.sendall(b'\0' * $SIZE); s.shutdown(socket.SHUT_WR); ack = s.recv(2); t2 = time.monotonic()
print(json.dumps({'connect_s': round(t1 - t0, 2), 'total_s': round(t2 - t0, 2), 'ok': ack == b'ok'}))
" > "$OUT.tmp"
wait "$SCHED" || true
docker exec os-ws-gw pkill -f "transfer_test.py serve" 2>/dev/null || true

python3 - "$OUT.tmp" "$OUT" "$GAP" "$START_AFTER" "$SIZE" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
gap, start_after, size = float(sys.argv[3]), float(sys.argv[4]), int(sys.argv[5])
r.update({"gap_s": gap, "upload_started_s_into_gap": start_after, "bytes": size,
          "wait_for_pass_s": gap - start_after,
          "after_pass_start_s": round(r["total_s"] - (gap - start_after), 2)})
json.dump(r, open(sys.argv[2], "w"), indent=2)
print(json.dumps(r, indent=2))
PY
rm -f "$OUT.tmp"
cat "results/gap_test_${TS}_schedule.log"
