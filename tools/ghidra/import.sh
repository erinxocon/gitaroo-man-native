#!/bin/bash
# Creates the Ghidra project build/ghidra/Gitaroo from iso/SLUS_202.94 and runs auto-analysis.
# Needs Ghidra 12.1.x with the ghidra-emotionengine-reloaded extension (R5900 language) installed.
# Run once; tools/ghidra/export.sh then applies config/symbols/ and exports build/functions.csv.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GHIDRA="${GHIDRA_INSTALL_DIR:-$HOME/tools/ghidra_12.1.4_PUBLIC}"
cd "$ROOT"
mkdir -p build/ghidra logs
"$GHIDRA/support/analyzeHeadless" build/ghidra Gitaroo -import iso/SLUS_202.94 \
    -processor r5900:LE:32:default -cspec default -overwrite -max-cpu "$(nproc)" \
    > logs/ghidra_import.log 2>&1
tail -3 logs/ghidra_import.log
