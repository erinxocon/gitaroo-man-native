#!/bin/bash
# Usage: tools/run/bench.sh <name> [perf]
# Boots straight into stage 00 (movies skipped, scripted menu input), prints the game's frame rate
# (GS FINISH per second) per 2s window, and with "perf" records a 20s profile of the stage.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NAME="$1"; PERF="${2:-}"
cd "$ROOT"
tools/run/run.sh "$NAME" 140 PS2X_SKIP_MOVIES=1 PS2X_DUMP_EVERY=100000 PS2X_TRACE_GS=1 \
    PS2X_INPUT_SCRIPT="$(cat tools/run/inputs/singleplay.txt)" &
if [ "$PERF" = perf ]; then
    sleep 100
    sudo perf record -F 499 -p "$(pgrep -x ps2EntryRunner)" -o "build/$NAME.perf" -- sleep 20 >/dev/null 2>&1
fi
wait
grep "trace:ee\]" "logs/$NAME.log" | paste - - | awk '{
    for (i = 1; i <= NF; i++) { if ($i ~ /^vsync=/) v = substr($i, 7); if ($i ~ /^finish=/) f = substr($i, 8) }
    if (pv != "") printf "%s:%.1f ", v, (f - pf) / (v - pv) * 60; pv = v; pf = f } END { print "" }'
if [ "$PERF" = perf ]; then
    sudo perf report -i "build/$NAME.perf" --no-children --sort sym --stdio 2>/dev/null | grep "%" | head -25
fi
