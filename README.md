# Gitaroo Man Native

A native PC port of **Gitaroo Man** (PS2, NTSC-U, SLUS-20294) built by *static recompilation*: the
game's MIPS R5900 executable is translated to C++ ahead of time and compiled together with a runtime
that stands in for the rest of the console. No game code or data is in this repository: you supply
your own disc, and the tools here produce everything else on your machine.

## Goals

- **As native as possible.** Like the [Ace Combat 5 static recompilation](https://github.com/sal063/Ace-Combat-5-Static-recompilation),
  replace emulated hardware with native code wherever we can: the VU1 microprogram is already
  recompiled to C++, and native (GPU) rendering of the game's meshes is the next big step.
- **Portable.** The shipped runtime should build and run on any platform a C++20 compiler and the
  runtime's libraries support. Platform-specific code is a last resort; dev/profiling tools may be
  platform-specific.
- **Correct by construction.** Every replacement is checked against a reference: the interpreter
  (lockstep verification), PCSX2 captures, or the original code.
- **Reproducible.** The repository holds only tooling, configuration and documentation; anyone with
  the disc can rebuild the same port.

## Status

Boots through the intro movies (video and audio), title screen and menus into the tutorial and the
stage 1 battle, playable with keyboard or controller, with music and sound. The VU1 renderer
microprogram runs as recompiled C++ (verified bit-for-bit against the interpreter over 10M+ runs).
The tutorial runs at a locked 60 fps; the stage 1 battle at 30-60 fps, limited by the software GS
rasterizer. Later stages are not yet tested in the port. See [docs/](docs/) for details.

## How it works

```
your disc ──► iso/SLUS_202.94 ──► Ghidra (function list) ──► ps2_recomp ──► build/recomp/*.cpp
                    │                                                              │
                    └──► tools/vu1lift ──► lifted VU1 microprogram (C++) ──────────┤
                                                                                   ▼
   external/PS2Recomp/ps2xRuntime (EE kernel HLE, IOP emulator, VIF/GIF/GS, VU1, SPU2, MPEG) + src/
                                                                                   │
                                                                                   ▼
                                                                 build/runtime/.../ps2EntryRunner
```

- **[external/PS2Recomp](https://github.com/erinxocon/PS2Recomp)** (git submodule, branch
  `gitaroo-native`): our fork of [ran-j/PS2Recomp](https://github.com/ran-j/PS2Recomp), the
  recompiler and runtime, with the fixes and features this game needed (GPL-3.0).
- **Ghidra** finds the functions; `config/symbols/` names the ones we have identified.
- **`ps2_recomp`** turns every function into C++ (`config/gitaroo.toml`).
- **`tools/vu1lift`** compiles the game's single VU1 microprogram to C++ (Ace Combat 5's
  `vurecomp` approach, adapted using a runtime census and PCSX2 captures).

## Requirements

- Linux (the only platform tested so far), a C++20 compiler (GCC 13+ or Clang 17+), CMake 3.21+, pkg-config,
  Ninja, Python 3, rsync, and 7z or bsdtar. FFmpeg development libraries (`libavcodec`,
  `libavformat`, `libavutil`, `libswscale`, `libswresample`) for the movies. Other runtime
  dependencies (raylib, ...) are fetched by CMake.
- [Ghidra](https://ghidra-sre.org/) 12.1.x with JDK 25 and the
  [ghidra-emotionengine-reloaded](https://github.com/chaoticgd/ghidra-emotionengine-reloaded)
  extension (R5900 support). Set `GHIDRA_INSTALL_DIR` and `JAVA_HOME` if they are not in
  `~/tools/ghidra_12.1.4_PUBLIC` / `~/jdk-25.0.4.1+1`.
- Your own copy of Gitaroo Man (NTSC-U). Optionally a PS2 BIOS and PCSX2 for reference captures.

## Building

```sh
git clone --recurse-submodules https://github.com/erinxocon/gitaroo-man-native.git
cd gitaroo-man-native

tools/build/extract_iso.sh "/path/to/Gitaroo Man.iso"   # -> iso/ (your disc's files)
tools/build/configure.sh                                 # builds the recompiler, configures the runtime
tools/ghidra/import.sh                                   # one-time Ghidra project + analysis (slow)
tools/ghidra/export.sh                                   # apply config/symbols, export build/functions.csv
python3 tools/vu1lift/vu1lift.py                         # optional: recompiled VU1 microprogram
tools/build/recomp.sh                                    # recompile the game and build the runner
```

On x86-64 the build targets your own CPU (`-march=native`); for binaries that run on other
machines use `PS2X_ARCH_FLAGS=-march=x86-64-v2 tools/build/configure.sh`.

`recomp.sh` writes the recompiled game to `build/recomp/` and builds
`build/runtime/ps2xRuntime/ps2EntryRunner`. Rerun it after changing `config/` or the runtime; run
`vu1lift.py` again (then `ninja -C build/runtime`) after changing the lifter. Without the lifted
microprogram the runtime simply uses the VU1 interpreter.

## Running

```sh
build/runtime/ps2xRuntime/ps2EntryRunner iso/SLUS_202.94
```

Keyboard: arrows = d-pad, WASD = left stick, IJKL = right stick, X/Space = Cross, C = Circle,
Z = Square, V = Triangle, Q/E = L1/R1, Shift = L2/R2, Enter = Start, Tab = Select. Controllers work
through SDL mappings (`SDL_GAMECONTROLLERCONFIG`, `PS2X_GAMECONTROLLERDB`).

`tools/run/run.sh <name> <seconds> [VAR=value ...]` runs headless-ish test sessions with a timeout,
logging to `logs/<name>.log` and dumping frames to `build/runs/<name>/`. Scripted input lives in
`tools/run/inputs/` (`button@tick:duration`, comma separated):

```sh
tools/run/run.sh stage1 900 PS2X_SKIP_MOVIES=1 PS2X_INPUT_SCRIPT="$(cat tools/run/inputs/stage1_battle.txt)"
tools/run/bench.sh tutorial            # frame rate per 2 s window in the tutorial
```

### Useful environment variables

| variable | effect |
|---|---|
| `PS2X_SKIP_MOVIES=1` | skip FMVs |
| `PS2X_INPUT_SCRIPT=...` | scripted pad input (`start@900:8,cross@1400:8`) |
| `PS2X_DUMP_FRAMES=<dir>`, `PS2X_DUMP_EVERY=N` | write every Nth presented frame as PNG |
| `PS2X_GS_THREADS=N` | GS rasterizer worker threads (default 6, 0 = inline) |
| `PS2X_DEINTERLACE=0` | show raw fields instead of the weaver |
| `PS2X_VU1_LIFT=0` | use the VU1 interpreter instead of the recompiled microprogram |
| `PS2X_VU1_VERIFY=1` | run interpreter and recompiled VU1 in lockstep and report any difference |
| `PS2X_VU1_CENSUS=<dir>` | record VU1 programs/entries/jump targets (AC5-format `manifest.json`) |
| `PS2X_NO_EE_INTERP=1` | disable the fallback EE interpreter for runtime-generated code |
| `PS2X_TRACE_GS`, `_VIF`, `_VU1`, `_RPC`, `_PAD`, `_SPU2`, `_TRX`, `_PRESENT`, `_DMACHAIN`, `_IOP_IMPORTS`, `_IOPMEM`, `_MPEG_AUDIO` | subsystem traces |
| `PS2X_DUMP_RAM_TICK`, `PS2X_DUMP_VRAM`, `PS2X_DUMP_TEX`, `PS2X_AUDIO_DUMP`, `PS2X_SPU2_INPUT_DUMP` | state dumps |

## Repository layout

| path | contents |
|---|---|
| `external/PS2Recomp/` | submodule: recompiler + runtime fork (GPL-3.0) |
| `config/gitaroo.toml` | recompiler configuration (stubs, native functions, patches) |
| `config/symbols/` | identified function names: `sdk.txt` (Sony SDK), `manual.txt`, `agent.txt` |
| `config/vu1_lift.json` | VU1 lifter input: entry points, jump targets, in-stream patched branches |
| `src/` | game-specific runtime code compiled into the runner (function overrides) |
| `tools/build/` | ISO extraction, CMake configure, recompile + build |
| `tools/ghidra/` | Ghidra project import, symbol application and function export |
| `tools/run/` | run/benchmark/profile scripts and scripted input |
| `tools/vu1lift/` | VU1 microprogram lifter and census comparison |
| `tools/pcsx2/` | PCSX2 launcher, PINE client and capture analysis |
| `tools/analysis/` | one-off analysis helpers (IRX disassembler, GS replay, audio comparison, ...) |
| `docs/` | architecture notes, VU1 lifter, PCSX2 reference workflow, research |

Ignored, local-only: `iso/` (your disc), `build/` (all generated output), `logs/`.

## Documentation

- [docs/architecture.md](docs/architecture.md): the game's engine and code layout
- [docs/vu1-lifter.md](docs/vu1-lifter.md): VU1 census, comparison with Ace Combat 5, the lifter and its verification
- [docs/pcsx2-reference.md](docs/pcsx2-reference.md): using PCSX2 as ground truth
- [docs/research/persona4-decomp.md](docs/research/persona4-decomp.md): lessons from the Persona 4 decompilation (same compiler family)

## Legal

This project contains no copyrighted game code or data and does not help obtain any. You need your
own legally obtained copy of the game. Gitaroo Man is a trademark of its respective owners; this
project is not affiliated with or endorsed by them.

Our own code and documentation are MIT licensed ([LICENSE](LICENSE)). The PS2Recomp submodule is
GPL-3.0, so binaries built from this repository are covered by the GPL-3.0.

## Acknowledgements

This project was built with the help of [Claude Code](https://claude.com/claude-code), which was
used and consulted throughout: reverse engineering, runtime fixes, the VU1 lifter and its
verification, tooling and documentation.
