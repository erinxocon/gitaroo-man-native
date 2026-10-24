#!/bin/bash
# Usage: tools/run/stage.sh <stage 1-10> <name> <seconds> [extra env assignments...]
# Boots with movies skipped, loads bank 1 of the memory card in PS2X_MC_ROOT (a save with the stages
# unlocked, e.g. extracted from PCSX2 with tools/pcsx2/mcextract.py), picks the stage on the Single Play
# map and lets it play out with no input (the battle is lost, then "Try again?" stays up).
# Output as tools/run/run.sh: logs/<name>.log, frames in build/runs/<name>/.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STAGE="$1"; NAME="$2"; SECS="$3"; shift 3
: "${PS2X_MC_ROOT:?set PS2X_MC_ROOT to a memory card directory holding a save with the stages unlocked}"
SCRIPT="$(cat "$ROOT/tools/run/inputs/load_save.txt")"
# The map cursor starts on stage 1; right steps to the next stage.
tick=6600
for ((i = 1; i < STAGE; i++)); do
    SCRIPT+=",right@$tick:8"
    tick=$((tick + 90))
done
SCRIPT+=",cross@$((tick + 200)):8"
exec "$ROOT/tools/run/run.sh" "$NAME" "$SECS" PS2X_SKIP_MOVIES=1 PS2X_MC_ROOT="$PS2X_MC_ROOT" PS2X_INPUT_SCRIPT="$SCRIPT" "$@"
