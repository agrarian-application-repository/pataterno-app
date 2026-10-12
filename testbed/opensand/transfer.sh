#!/usr/bin/env bash
# transfer.sh - time application-sized uploads ws_st -> ws_gw across the satellite
# (1 KB, 10 KB, 150 KB, 1 MB, 5 MB; fresh TCP connection each, REPS samples).
# Output: results/transfer_<timestamp>.json and results/transfer_latest.json
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
mkdir -p results
REPS="${REPS:-3}"
PORT=5599
TS="$(date +%Y%m%dT%H%M%S)"

docker cp transfer_test.py os-ws-gw:/tmp/transfer_test.py >/dev/null
docker cp transfer_test.py os-ws-st:/tmp/transfer_test.py >/dev/null
docker exec os-ws-gw pkill -f "transfer_test.py serve" 2>/dev/null || true
docker exec -d os-ws-gw python3 /tmp/transfer_test.py serve "$PORT"
sleep 1
docker exec os-ws-st python3 /tmp/transfer_test.py run 192.168.1.2 "$PORT" "$REPS" > "results/transfer_${TS}.json"
docker exec os-ws-gw pkill -f "transfer_test.py serve" 2>/dev/null || true
cp "results/transfer_${TS}.json" results/transfer_latest.json

python3 - "results/transfer_${TS}.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(f"{'payload':>10} | {'median s':>8} | {'min-max s':>13} | goodput Mbit/s")
for r in d["results"]:
    b = r["bytes"]
    label = f"{b/1e6:g} MB" if b >= 1e6 else f"{b/1e3:g} KB"
    print(f"{label:>10} | {r['median_total_s']:>8.2f} | {r['min_total_s']:>5.2f}-{r['max_total_s']:<6.2f} | {r['goodput_mbps_median']:.3f}")
PY
echo "saved: results/transfer_${TS}.json"
