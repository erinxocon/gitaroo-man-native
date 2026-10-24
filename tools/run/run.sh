#!/bin/bash
# Usage: tools/run/run.sh <name> <seconds> [extra env assignments...]
# Runs the recompiled game with a timeout, logging to logs/<name>.log and dumping frames to build/runs/<name>/.
# PS2X_RUNNER=<binary> runs another build (e.g. a copy, so long batches survive rebuilds).
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NAME="$1"; SECS="$2"; shift 2
OUT="$ROOT/build/runs/$NAME"
mkdir -p "$OUT" "$ROOT/logs"
cd "$ROOT"
env PS2X_DUMP_FRAMES="$OUT" "$@" timeout "$SECS" "${PS2X_RUNNER:-$ROOT/build/runtime/ps2xRuntime/ps2EntryRunner}" "$ROOT/iso/SLUS_202.94" > "$ROOT/logs/$NAME.log" 2>&1
echo "exit=$? frames=$(ls "$OUT" | wc -l) log=logs/$NAME.log"
