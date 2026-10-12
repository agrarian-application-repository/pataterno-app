#!/usr/bin/env bash
# logs.sh - summary of the OpenSAND logs of sat, gw and st.
#   ./logs.sh          error/event counts per type, plus process state
#   ./logs.sh tail     last 15 lines of each log
#   ./logs.sh mark     print current line counts (to diff before/after a test)
# Known, harmless entries: "connectToQoSServer ... TCP is not available"
# (no QoS server in this setup, every 5 s on st) and, right after a link
# gap from leo_passes.sh, "we may have lost UDP packets" (carrier sequence gap).
set -uo pipefail
for n in sat gw st; do
  c="os-$n"
  echo "== $n ($(docker exec "$c" pgrep -x opensand >/dev/null && echo running || echo NOT running))"
  case "${1:-}" in
    tail) docker exec "$c" tail -15 /var/log/opensand.log ;;
    mark) docker exec "$c" wc -l /var/log/opensand.log ;;
    *)    docker exec "$c" bash -c "grep -o -E '\]\[(ERROR|WARNING|EVENT)\]\[opensand\]\[[^]]*\]' /var/log/opensand.log | sort | uniq -c | sort -rn | head -8" ;;
  esac
done
