#!/usr/bin/env bash
# down.sh - stop the local OpenSAND testbed and remove its wiring.
# Application containers attached with network_mode "service:ws_gw" /
# "service:ws_st" (or "container:os-ws-gw") must be stopped first.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

for c in os-sat os-gw os-st; do
  docker exec "$c" killall -q opensand 2>/dev/null || true
done
docker compose down --remove-orphans
ip netns del pt-emu 2>/dev/null || true
echo "[down] testbed stopped"
