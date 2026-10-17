#!/bin/bash
# Poor man's profiler: samples all thread stacks of the running ps2EntryRunner N times.
# Usage: tools/run/gdbprof.sh <samples> <out>
P=$(pgrep -x ps2EntryRunner | head -1)
[ -z "$P" ] && { echo "not running"; exit 1; }
for i in $(seq "$1"); do
  sudo gdb -p "$P" -batch -ex 'thread apply all bt 25' 2>/dev/null | grep -E '^#|^Thread'
  echo "===="
  sleep 0.3
done > "$2"
