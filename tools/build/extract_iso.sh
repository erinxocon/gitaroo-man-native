#!/bin/bash
# Usage: tools/build/extract_iso.sh <path/to/your/Gitaroo Man (USA).iso>
# Extracts your own copy of the disc (SLUS-20294) into iso/, which the recompiler (iso/SLUS_202.94)
# and the runtime (game files next to the ELF) read. Uses 7z or bsdtar, whichever is installed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ISO="${1:?usage: $0 <disc image .iso>}"
mkdir -p "$ROOT/iso"
if command -v 7z >/dev/null; then
    7z x -y -o"$ROOT/iso" "$ISO" >/dev/null
elif command -v bsdtar >/dev/null; then
    bsdtar -xf "$ISO" -C "$ROOT/iso"
else
    echo "need 7z or bsdtar to extract the ISO" >&2
    exit 1
fi
test -f "$ROOT/iso/SLUS_202.94" || { echo "iso/SLUS_202.94 not found: is this the NTSC-U disc?" >&2; exit 1; }
sha1sum "$ROOT/iso/SLUS_202.94"
