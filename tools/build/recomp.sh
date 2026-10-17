#!/bin/bash
# Regenerates C++ from the game ELF using config/gitaroo.toml (paths in it are relative to the repo
# root and made absolute here), then rebuilds the runner.
# Output is generated into a scratch dir and synced by content, so stale files are removed and
# unchanged files keep their timestamps (only changed translation units rebuild).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RECOMP="${PS2_RECOMP:-$ROOT/build/tools/ps2xRecomp/ps2_recomp}"
cd "$ROOT"
rm -rf build/recomp.new
mkdir -p build/recomp.new build/recomp logs
sed -e "s#^input = \"#input = \"$ROOT/#" \
    -e "s#^ghidra_output = \"#ghidra_output = \"$ROOT/#" \
    -e "s#^output = .*#output = \"$ROOT/build/recomp.new\"#" config/gitaroo.toml > build/gitaroo.gen.toml
"$RECOMP" build/gitaroo.gen.toml > logs/recomp.log 2>&1
tail -1 logs/recomp.log
rsync -a --checksum --delete build/recomp.new/ build/recomp/
rm -rf build/recomp.new
ninja -C build/runtime -j"$(nproc)" ps2EntryRunner > logs/build_runtime.log 2>&1 || { grep -m20 'error' logs/build_runtime.log; exit 1; }
tail -1 logs/build_runtime.log
