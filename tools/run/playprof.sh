#!/bin/bash
# Usage: tools/run/playprof.sh <name>
# Runs the game normally for interactive play while perf samples it; writes logs/<name>.log (with the
# periodic trace:ee frame counters) and build/<name>.perf. Prints the frame rate per 2s window on exit.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NAME="$1"
cd "$ROOT"
mkdir -p logs
# perf launches the game itself so it follows threads created later (-p only sees existing ones);
# running unprivileged needs kernel.perf_event_paranoid <= 1.
[ "$(cat /proc/sys/kernel/perf_event_paranoid)" -le 1 ] || sudo sysctl -qw kernel.perf_event_paranoid=-1 kernel.kptr_restrict=0
PS2X_TRACE_GS=1 perf record -q -F 199 -o "build/$NAME.perf" -- \
    build/runtime/ps2xRuntime/ps2EntryRunner iso/SLUS_202.94 > "logs/$NAME.log" 2>&1
grep "trace:ee\]" "logs/$NAME.log" | paste - - | awk '{
    for (i = 1; i <= NF; i++) { if ($i ~ /^vsync=/) v = substr($i, 7); if ($i ~ /^finish=/) f = substr($i, 8) }
    if (pv != "" && v > pv) printf "%s:%.1f ", v, (f - pf) / (v - pv) * 60; pv = v; pf = f } END { print "" }'
echo "profile: build/$NAME.perf  log: logs/$NAME.log"
