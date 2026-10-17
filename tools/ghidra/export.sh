#!/bin/bash
# Applies SDK + manual symbols to the Ghidra project and re-exports build/functions.csv.
# The exporter's TOML goes to build/ so the curated config/gitaroo.toml is not overwritten.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GHIDRA="${GHIDRA_INSTALL_DIR:-$HOME/tools/ghidra_12.1.4_PUBLIC}"
export JAVA_HOME="${JAVA_HOME:-$HOME/jdk-25.0.4.1+1}"
cd "$ROOT"
grep -v '^#' config/symbols/manual.txt | grep . > build/symbols_all.txt
cat config/symbols/sdk.txt >> build/symbols_all.txt
# Agent-identified names. The game's sceDmaSyncN/sceDmaCallback are print-only stubs, so keep them
# from matching the functional runtime handlers.
grep -v '^#' config/symbols/agent.txt | grep . | sed -E 's/^(sceDmaSyncN|sceDmaCallback) /\1_printstub /' >> build/symbols_all.txt
"$GHIDRA/support/analyzeHeadless" build/ghidra Gitaroo -process SLUS_202.94 -noanalysis \
  -scriptPath "tools/ghidra;$ROOT/external/PS2Recomp/ps2xRecomp/tools/ghidra" \
  -postScript ApplySymbols.java "$ROOT/build/symbols_all.txt" \
  -postScript ExportPS2Functions.java "$ROOT/build/ghidra_export.toml" "$ROOT/build/functions.csv" \
  > logs/ghidra_export.log 2>&1
grep -E 'ApplySymbols:|Exported [0-9]+ functions' logs/ghidra_export.log
