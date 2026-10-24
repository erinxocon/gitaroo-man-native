#!/bin/bash
# Configures and builds the two CMake trees from external/PS2Recomp:
#   build/tools    the recompiler (ps2_recomp) and analyzer
#   build/runtime  the runtime + ps2EntryRunner, compiled together with build/recomp (the recompiled
#                  game, produced by tools/build/recomp.sh) and src/ (our game-specific code)
# Extra arguments are passed to both cmake configure calls (e.g. -DPS2X_PROFILE_DEBUGINFO=ON).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="$ROOT/external/PS2Recomp"
test -f "$SRC/CMakeLists.txt" || { echo "external/PS2Recomp is empty: git submodule update --init" >&2; exit 1; }
cmake -S "$SRC" -B "$ROOT/build/tools" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DPS2X_BUILD_RECOMP=ON -DPS2X_BUILD_ANALYZER=ON -DPS2X_BUILD_RUNTIME=OFF "$@"
cmake --build "$ROOT/build/tools"
mkdir -p "$ROOT/build/recomp"
cmake -S "$SRC" -B "$ROOT/build/runtime" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DPS2X_BUILD_RUNTIME=ON -DPS2X_BUILD_RECOMP=OFF -DPS2X_BUILD_ANALYZER=OFF \
    -DPS2X_GAME_SOURCE_DIR="$ROOT/src" -DPS2X_GENERATED_DIR="$ROOT/build/recomp" "$@"
