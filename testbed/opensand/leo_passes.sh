#!/usr/bin/env bash
# leo_passes.sh - impose a LEO-style visibility schedule on the satellite
# terminal of the local OpenSAND testbed.
#
# OpenSAND (as configured by the NCSRD guide) emulates one always-visible
# satellite. A LEO terminal instead sees a satellite only during passes.
# This script alternates "pass" (link up) and "gap" (link down) periods by
# applying netem on BOTH directions of the terminal's emulation interface:
#   uplink   : os-st,  dev emu      (terminal -> satellite)
#   downlink : pt-emu, dev emu-st   (satellite -> terminal)
# A gap is netem "loss 100%": the OpenSAND processes keep running, nothing
# crosses. Optional extra impairments during passes: --delay, --loss.
#
# This is a schedule approximation, not orbital mechanics: pass length and
# gap length are inputs (to be replaced by Testbed 2 / DLR parameters).
#
# Usage (as root in WSL/Linux, testbed up):
#   ./leo_passes.sh --up 600 --down 900 --cycles 4      # 10-min passes, 15-min gaps
#   ./leo_passes.sh --up 60 --down 120 --cycles 0       # loop until Ctrl-C
#   ./leo_passes.sh --up 300 --down 300 --delay 20 --loss 1
#   ./leo_passes.sh --start down --up 60 --down 90      # begin with a gap
#   ./leo_passes.sh --clear                             # remove all impairments
#   ./leo_passes.sh --status
# Every transition is logged (ISO time + epoch) to results/leo_passes.log,
# so application timestamps can be matched against link state.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$HERE/results/leo_passes.log"
mkdir -p "$HERE/results"

UP=600; DOWN=900; CYCLES=1; DELAY=""; LOSS=""; START=up; MODE=run
while [[ $# -gt 0 ]]; do
  case "$1" in
    --up) UP="$2"; shift 2 ;;
    --down) DOWN="$2"; shift 2 ;;
    --cycles) CYCLES="$2"; shift 2 ;;
    --delay) DELAY="$2"; shift 2 ;;
    --loss) LOSS="$2"; shift 2 ;;
    --start) START="$2"; shift 2 ;;
    --clear) MODE=clear; shift ;;
    --status) MODE=status; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done

up_tc()   { docker exec os-st tc "$@"; }
down_tc() { ip netns exec pt-emu tc "$@"; }

event() {
  local line; line="$(date -Is) $(date +%s.%N | cut -c1-14) $*"
  echo "$line" | tee -a "$LOG"
}

clear_all() {
  up_tc qdisc del dev emu root 2>/dev/null || true
  down_tc qdisc del dev emu-st root 2>/dev/null || true
}

link_down() {
  up_tc qdisc replace dev emu root netem loss 100%
  down_tc qdisc replace dev emu-st root netem loss 100%
}

link_up() {
  if [[ -n "$DELAY$LOSS" ]]; then
    local args=()
    [[ -n "$DELAY" ]] && args+=(delay "${DELAY}ms")
    [[ -n "$LOSS" ]] && args+=(loss "${LOSS}%")
    up_tc qdisc replace dev emu root netem "${args[@]}"
    down_tc qdisc replace dev emu-st root netem "${args[@]}"
  else
    clear_all
  fi
}

status() {
  echo "uplink   (os-st emu):     $(up_tc qdisc show dev emu | head -1)"
  echo "downlink (pt-emu emu-st): $(down_tc qdisc show dev emu-st | head -1)"
}

case "$MODE" in
  clear)  clear_all; event "CLEAR link restored (no impairment)"; exit 0 ;;
  status) status; exit 0 ;;
esac

trap 'clear_all; event "STOP schedule ended - link restored"' EXIT
trap 'exit 130' INT TERM

event "START schedule up=${UP}s down=${DOWN}s cycles=${CYCLES} delay=${DELAY:-0}ms loss=${LOSS:-0}% start=${START}"
i=0
while [[ "$CYCLES" == "0" || "$i" -lt "$CYCLES" ]]; do
  i=$((i+1))
  if [[ "$START" == "up" ]]; then
    link_up;   event "PASS_START cycle=$i duration=${UP}s";   sleep "$UP"
    link_down; event "GAP_START  cycle=$i duration=${DOWN}s"; sleep "$DOWN"
  else
    link_down; event "GAP_START  cycle=$i duration=${DOWN}s"; sleep "$DOWN"
    link_up;   event "PASS_START cycle=$i duration=${UP}s";   sleep "$UP"
  fi
done
