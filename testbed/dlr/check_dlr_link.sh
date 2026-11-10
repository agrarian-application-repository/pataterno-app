#!/usr/bin/env bash
# check_dlr_link.sh - the gateway -> Testbed 2 (DLR) path, step by step.
#
# Run it on the soil gateway (or any Linux host) with the DLR WireGuard tunnel
# up. It repeats the checks of the 19 October integration session, then talks
# to the PATATERNO container on the satellite edge host through the same path:
#
#   gateway (tb0 10.5.6.40 / tb1 10.5.6.41)
#     -> 10.5.6.10:8000        DLR VM; iptables DNAT to the input satellite gateway
#     -> gwy0 10.0.10.2:8000   -> sat0 -> edge host swc1 10.0.20.2:8001
#     -> container port 5001   (docker run -p 8001:5001 ...pataterno-app)
#
#   1  ping 10.5.6.10                         VPN and DLR VM reachable
#   2  raw TCP socket to 10.5.6.10:8000       something listens behind the forward
#   3  HTTP /health, /                        it is the PATATERNO container (version, image tag)
#   4  HTTP /dbcheck?stages=tcp               from INSIDE the container: is the data
#                                             repository (pataterno_db) reachable?
#   5  HTTP POST /images (optional, FRAME=)   one drone frame across the satellites
#
# Usage:  ./check_dlr_link.sh                       (defaults: 10.5.6.10:8000)
#         TARGET=10.5.6.10 PORT=8000 FRAME=frames/dorifera_spotted_2.png ./check_dlr_link.sh
#
# Note: tb0 on the gateway and the agrarian_p2_0 profile on a PC are the SAME
# WireGuard identity (10.5.6.40). Only one of them can be up at a time; use
# tb1 / agrarian_p2_1 (10.5.6.41) on the second machine.
set -u
TARGET="${TARGET:-10.5.6.10}"
PORT="${PORT:-8000}"
FRAME="${FRAME:-}"
BASE="http://$TARGET:$PORT"
pass=0; fail=0
ok()   { echo "  PASS  $*"; pass=$((pass + 1)); }
bad()  { echo "  FAIL  $*"; fail=$((fail + 1)); }
info() { echo "        $*"; }

echo "== 0  local side"
ip -4 -o addr show 2>/dev/null | awk '$4 ~ /^10\.5\.6\./ {print "        VPN address " $4 " on " $2}'
if command -v vpn >/dev/null 2>&1; then vpn status tb0 tb1 2>/dev/null | sed 's/^/        /'; fi

echo "== 1  ping $TARGET"
if ping -c 3 -W 3 "$TARGET" >/tmp/dlr_ping.txt 2>&1; then
  ok "$(tail -1 /tmp/dlr_ping.txt)"
else
  bad "no reply - is the DLR tunnel up? ($(tail -1 /tmp/dlr_ping.txt))"
fi

echo "== 2  raw TCP socket to $TARGET:$PORT"
python3 - "$TARGET" "$PORT" <<'PY'
import socket, sys, time
host, port = sys.argv[1], int(sys.argv[2])
s = socket.socket()
s.settimeout(10)
t = time.time()
try:
    s.connect((host, port))
except ConnectionRefusedError:
    print("  FAIL  refused - nothing listens behind the forward (DLR emulation or container not running)")
    sys.exit(1)
except OSError as e:
    print(f"  FAIL  {type(e).__name__}: {e}")
    sys.exit(1)
ms = round((time.time() - t) * 1000)
s.sendall(b"GET /health HTTP/1.0\r\nHost: pataterno\r\n\r\n")
try:
    reply = s.recv(1024).decode("latin-1", "replace")
except OSError:
    reply = ""
s.close()
first = reply.splitlines()[0] if reply else "(no reply)"
print(f"  PASS  connected in {ms} ms; first line of the reply: {first}")
PY
[ $? -eq 0 ] && pass=$((pass + 1)) || fail=$((fail + 1))

echo "== 3  HTTP $BASE/health and /"
if out=$(curl -fsS --max-time 20 "$BASE/health" 2>&1); then
  ok "/health -> $out"
  curl -fsS --max-time 20 "$BASE/" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("        " + d.get("name","?") + " " + d.get("version","?") + "  image " + str(d.get("image_tag")))' 2>/dev/null
else
  bad "/health: $out"
fi

echo "== 4  from inside the container: data repository (pataterno_db)"
if out=$(curl -fsS --max-time 60 "$BASE/dbcheck?stages=tcp" 2>&1); then
  echo "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
tcp = d.get("tcp") or {}
c = d.get("container") or {}
print("        verdict " + str(d.get("verdict")) + " | tcp reachable " + str(tcp.get("reachable"))
      + " | " + str(tcp.get("connect_ms") or tcp.get("ms") or "") + " ms | attempts " + str(tcp.get("attempts")))
print("        container host " + str(c.get("hostname")) + " | egress " + str(c.get("egress_ip")))
sys.exit(0 if tcp.get("reachable") else 1)
' && ok "pataterno_db reachable from inside the container" \
    || bad "pataterno_db NOT reachable from inside the container (see verdict above)"
else
  bad "/dbcheck: $out"
fi

if [ -n "$FRAME" ]; then
  echo "== 5  one drone frame across the satellites: $FRAME"
  t0=$(date +%s.%N)
  if out=$(curl -fsS --max-time 120 -X POST --data-binary @"$FRAME" -H "Content-Type: image/png" \
           "$BASE/images?frame=$(basename "$FRAME")&lat=40.33044&lon=15.60819" 2>&1); then
    t1=$(date +%s.%N)
    echo "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
k = d.get("kpi6") or {}
print("        " + str(d.get("crops")) + " crops, " + str(len(d.get("detections") or [])) + " detections, classified in "
      + str(d.get("classify_ms")) + " ms; frame " + str(k.get("raw_bytes")) + " B")'
    ok "frame processed at the edge in $(python3 -c "print(round($t1 - $t0, 2))") s round trip"
  else
    bad "/images: $out"
  fi
fi

echo "== summary: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
