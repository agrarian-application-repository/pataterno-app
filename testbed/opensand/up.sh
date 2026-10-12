#!/usr/bin/env bash
# up.sh - bring up the PATATERNO local OpenSAND testbed.
#
# Reproduces the NCSRD "Testbed 1" OpenSAND guide (v8.2.0, IP mode) with
# containers in place of the five VirtualBox VMs:
#   1. start the five node containers           (= create the VMs)
#   2. wire emu / lan_gw / lan_st with veths     (= vboxnet0 / vboxnet1 / vboxnet2)
#   3. "make network" in every node              (= User Guide section 2)
#   4. generate the XML and launch sat -> gw -> st (= User Guide section 4, same order)
#   5. wait until ws_st reaches ws_gw across the satellite
#
# Must run as root inside WSL/Linux (needs ip netns / nsenter).
# Env:
#   SAT_DELAY_MS  one-way delay per space hop, ms (default 125 = upstream
#                 profile; ground->sat->ground = 250 ms, RTT ~500 ms)
#   BUILD=1       rebuild the node image first
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

SAT_DELAY_MS="${SAT_DELAY_MS:-125}"
EMU_NS=pt-emu
CLI=/opt/opensand_cli/ip
NODES=(os-sat os-gw os-st os-ws-gw os-ws-st)

log() { printf '[up %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
pid_of() { docker inspect -f '{{.State.Pid}}' "$1"; }
cx() { local c="$1"; shift; docker exec "$c" bash -c "$*"; }

modprobe tun 2>/dev/null || true
modprobe sch_netem 2>/dev/null || true
# OpenSAND requests large UDP socket buffers for its carriers (SO_RCVBUF /
# SO_SNDBUF); the kernel silently caps them at rmem_max/wmem_max, which are
# host-wide (not settable inside the containers). Raise only the caps.
sysctl -qw net.core.rmem_max=16777216 net.core.wmem_max=16777216 || true

log "starting node containers"
if [[ "${BUILD:-0}" == "1" ]]; then
  docker compose up -d --build sat gw st ws_gw ws_st
else
  docker compose up -d sat gw st ws_gw ws_st
fi

declare -A P
for c in "${NODES[@]}"; do P[$c]="$(pid_of "$c")"; done

# ---- 2. links (idempotent: remove previous wiring first) -------------------
log "wiring emu (vboxnet0), lan_gw (vboxnet1), lan_st (vboxnet2)"
for c in os-sat os-gw os-st; do
  docker exec "$c" killall -q opensand 2>/dev/null || true
  nsenter -t "${P[$c]}" -n ip link del emu 2>/dev/null || true
done
for c in os-gw os-st os-ws-gw os-ws-st; do
  nsenter -t "${P[$c]}" -n ip link del lan 2>/dev/null || true
done
ip netns del "$EMU_NS" 2>/dev/null || true

# emu: a switch (bridge) in its own namespace, one port per OpenSAND entity
ip netns add "$EMU_NS"
ip -n "$EMU_NS" link set lo up
ip -n "$EMU_NS" link add br0 type bridge
ip -n "$EMU_NS" link set br0 up
for n in sat gw st; do
  ip -n "$EMU_NS" link add "emu-$n" type veth peer name emu netns "${P[os-$n]}"
  ip -n "$EMU_NS" link set "emu-$n" master br0 up
done

# lan_gw and lan_st: point-to-point
nsenter -t "${P[os-gw]}" -n ip link add lan type veth peer name lan netns "${P[os-ws-gw]}"
nsenter -t "${P[os-st]}" -n ip link add lan type veth peer name lan netns "${P[os-ws-st]}"

# Checksum offload off on every satellite-side interface. Frames that leave
# a veth with a deferred (partial) checksum would otherwise cross the
# OpenSAND TAP unchecksummed and be dropped by the far end - ping works,
# TCP silently does not.
cx os-sat "ethtool -K emu tx off >/dev/null"
for c in os-gw os-st; do cx "$c" "ethtool -K emu tx off >/dev/null; ethtool -K lan tx off >/dev/null"; done
for c in os-ws-gw os-ws-st; do cx "$c" "ethtool -K lan tx off >/dev/null"; done

# ---- 3. make network (CNES Makefiles, interface names passed as variables) --
log "make network on sat, gw, st, ws_gw, ws_st"
cx os-sat   "make -s -C $CLI/sat   network EMU_IFACE=emu"                >/dev/null
cx os-gw    "make -s -C $CLI/gw    network EMU_IFACE=emu LAN_IFACE=lan"  >/dev/null
cx os-st    "make -s -C $CLI/st    network EMU_IFACE=emu LAN_IFACE=lan"  >/dev/null
cx os-ws-gw "make -s -C $CLI/ws_gw network LAN_IFACE=lan"                >/dev/null
cx os-ws-st "make -s -C $CLI/ws_st network LAN_IFACE=lan"                >/dev/null

# ---- 4. XML + launch, order sat -> gw -> st -----------------------------
log "generating XML (collector off, delay ${SAT_DELAY_MS} ms per hop)"
for n in sat gw st; do
  cx "os-$n" "make -s -C $CLI/$n generate-xml ENABLE_COLLECTOR=false >/dev/null"
done
# Two run-time changes to the upstream profile, written to profile_run.xml:
#  - delay_value: the per-hop delay (SAT_DELAY_MS, upstream 125);
#  - decap_buffer_len 8 -> 5000: the CNES CLI example ships 8 (bytes - the
#    "Maximal Packet length" of the GSE decapsulator), which makes the
#    terminal drop every real packet with "DecapErrorSizePduBuffer".
#    5000 is the value opensand-deploy writes into its default profile,
#    i.e. what the NCSRD guide's web-UI "default.xml" produces.
for n in gw st; do
  cx "os-$n" "sed -E -e 's|<delay_value>[0-9]+</delay_value>|<delay_value>${SAT_DELAY_MS}</delay_value>|' -e 's|<decap_buffer_len>[0-9]+</decap_buffer_len>|<decap_buffer_len>5000</decap_buffer_len>|' $CLI/$n/profile.xml > $CLI/$n/profile_run.xml"
done

launch() {
  local n="$1" prof="$2"
  docker exec -d "os-$n" bash -c "cd $CLI/$n && exec opensand -v -i infrastructure_updated.xml -t topology_updated.xml $prof > /var/log/opensand.log 2>&1"
}
log "launching OpenSAND: sat"; launch sat ""; sleep 2
log "launching OpenSAND: gw";  launch gw "-p profile_run.xml"; sleep 2
log "launching OpenSAND: st";  launch st "-p profile_run.xml"

# ---- 5. wait for the terminal to log on and carry traffic -----------------
log "waiting for ws_st -> ws_gw across the satellite"
for i in $(seq 1 60); do
  if docker exec os-ws-st ping -c1 -W3 192.168.1.2 >/dev/null 2>&1; then
    log "link up after ~$((i*3)) s"
    docker exec os-ws-st ping -c3 -i0.5 192.168.1.2 | tail -2
    log "testbed ready - ws_gw 192.168.1.2 (host port 18080), ws_st 192.168.2.2"
    exit 0
  fi
  for n in sat gw st; do
    if ! docker exec "os-$n" pgrep -x opensand >/dev/null; then
      log "ERROR: opensand exited on $n - last log lines:"
      docker exec "os-$n" tail -20 /var/log/opensand.log
      exit 1
    fi
  done
  sleep 2
done
log "ERROR: no traffic across the satellite after the timeout"
for n in sat gw st; do echo "--- $n"; docker exec "os-$n" tail -15 /var/log/opensand.log; done
exit 1
