#!/bin/bash
# Usage: tools/pcsx2/pcsx2.sh [extra pcsx2-qt args...]
# Boots GITAROOMAN.ISO in the PCSX2 flatpak with the PINE socket visible to the host, so
# tools/pcsx2/pine.py can read memory / trigger save states while the game runs.
# The app log goes to logs/pcsx2_<timestamp>.log. Examples of extra args: -debugger, -fastboot, -statefile <p2s>.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HOST_RUN="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
# $XDG_RUNTIME_DIR inside the sandbox is a private tmpfs; app/<id> is the one subdir shared with the
# host, so PINE creates pcsx2.sock there. Wayland must then be given as an absolute path.
APP_RUN="$HOST_RUN/app/net.pcsx2.PCSX2"
LOG="$ROOT/logs/pcsx2_$(date +%Y%m%d_%H%M%S).log"
mkdir -p "$ROOT/logs"
echo "log=$LOG socket=$APP_RUN/pcsx2.sock"
exec flatpak run \
    --filesystem="$ROOT" \
    --env=XDG_RUNTIME_DIR="$APP_RUN" \
    --env=WAYLAND_DISPLAY="$HOST_RUN/${WAYLAND_DISPLAY:-wayland-0}" \
    net.pcsx2.PCSX2 -logfile "$LOG" "$@" -- "$ROOT/GITAROOMAN.ISO"
