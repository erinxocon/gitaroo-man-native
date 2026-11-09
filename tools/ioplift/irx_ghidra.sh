#!/bin/bash
# Imports the relocated sound modules (tools/ioplift/irx_symbols.py) into the Ghidra project
# build/ghidra/IopSound, applies their symbols, analyses them and writes pseudo-C to
# build/irx/<NAME>.c. Derived from the game: never commit the outputs.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GHIDRA="${GHIDRA_INSTALL_DIR:-$HOME/tools/ghidra_12.1.4_PUBLIC}"
export JAVA_HOME="${JAVA_HOME:-$HOME/jdk-25.0.4.1+1}"
cd "$ROOT"
python3 tools/ioplift/irx_symbols.py
mkdir -p build/ghidra logs
for spec in LIBSD:0x44900 SDRDRV:0x49200 MSTREAM:0x4AA00; do
    name="${spec%%:*}" base="${spec##*:}"
    "$GHIDRA/support/analyzeHeadless" build/ghidra IopSound -import "build/irx/$name.bin" -overwrite \
        -loader BinaryLoader -loader-baseAddr "$base" -processor MIPS:LE:32:default -cspec default \
        -scriptPath tools/ghidra \
        -preScript ApplySymbols.java "$ROOT/build/irx/$name.sym" \
        -postScript DumpDecompilation.java "$ROOT/build/irx/$name.c" \
        > "logs/ghidra_irx_$name.log" 2>&1
    echo "$name: $(grep -c '^// =====' "build/irx/$name.c") functions -> build/irx/$name.c"
done
