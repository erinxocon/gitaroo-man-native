# Gitaroo Man Native

A native PC port of **Gitaroo Man** (PS2, NTSC-U, SLUS-20294) built by *static recompilation*: the
game's MIPS R5900 executable is translated to C++ ahead of time and compiled together with a runtime
that stands in for the rest of the console. No game code or data is in this repository: you supply
your own disc, and the tools here produce everything else on your machine.

## Goals

- **As native as possible.** Like the [Ace Combat 5 static recompilation](https://github.com/sal063/Ace-Combat-5-Static-recompilation),
  replace emulated hardware with native code wherever we can: the VU1 mesh renderer is native C++
  (the rest of the microprogram is recompiled to C++) and the GS is drawn with OpenGL; drawing the
  game's meshes directly on the GPU is the next big step.
- **Portable.** The shipped runtime should build and run on any platform a C++20 compiler and the
  runtime's libraries support. Platform-specific code is a last resort; dev/profiling tools may be
  platform-specific.
- **Correct by construction.** Every replacement is checked against a reference: the interpreter
  (lockstep verification), PCSX2 captures, or the original code.
- **Reproducible.** The repository holds only tooling, configuration and documentation; anyone with
  the disc can rebuild the same port.

## Status

Boots through the intro movies (video and audio), title screen and menus into the tutorial and the
stage 1 battle, playable with keyboard or controller, with music and sound. The VU1 mesh renderer
(transform, lighting, clip test) is native C++, the rest of the microprogram recompiled C++, both
verified bit-for-bit against the interpreter over 10M+ runs. Sound is native: the game's sound
functions are served by host code that reads the music and effects from the disc's sound libraries
and mixes them ([docs/native-audio.md](docs/native-audio.md)). Graphics are drawn with OpenGL at 2x
(or more) internal resolution, verified frame by frame against the CPU rasterizer. The tutorial and
the whole stage 1 battle run at a locked 60 fps. Optional 16:9 widescreen. Stages 2-10 start from a
completed memory card save (`tools/run/stage.sh`) and run with both verifiers on: VU1 bit-exact on
all of them (70-110M runs each), GPU frames matching the CPU rasterizer except a few effects listed
in [docs/gpu-renderer.md](docs/gpu-renderer.md). The sound card drives the mix (about 15 ms from mix to
speaker), so a slow game frame does not stall the music. See [docs/](docs/) for details.

## How it works

```
your disc ──► iso/SLUS_202.94 ──► Ghidra (function list) ──► ps2_recomp ──► build/recomp/*.cpp
                    │                                                              │
                    └──► tools/vu1lift ──► lifted VU1 microprogram (C++) ──────────┤
                                                                                   ▼
   external/PS2Recomp/ps2xRuntime (EE kernel HLE, IOP emulator, VIF/GIF/GS, VU1, SPU2, MPEG) + src/
                                                                                   │
                                                                                   ▼
                                                                 build/game/gitaroo-man
```

- **[external/PS2Recomp](https://github.com/erinxocon/PS2Recomp)** (git submodule, branch
  `gitaroo-native`): our fork of [ran-j/PS2Recomp](https://github.com/ran-j/PS2Recomp), the
  recompiler and runtime, with the fixes and features this game needed (GPL-3.0).
- **Ghidra** finds the functions; `config/symbols/` names the ones we have identified.
- **`ps2_recomp`** turns every function into C++ (`config/gitaroo.toml`).
- **`tools/vu1lift`** compiles the game's single VU1 microprogram to C++ (Ace Combat 5's
  `vurecomp` approach, adapted using a runtime census and PCSX2 captures).
- **`tools/ioplift`** compiles the IOP sound drivers (MSTREAM, SDRDRV, LIBSD) from MIPS to C++.

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
python3 tools/ioplift/ioplift.py                         # optional: IOP sound drivers as C++
tools/build/recomp.sh                                    # recompile the game and build it
```

On x86-64 the build targets your own CPU (`-march=native`); for binaries that run on other
machines use `PS2X_ARCH_FLAGS=-march=x86-64-v2 tools/build/configure.sh`.

`recomp.sh` writes the recompiled game to `build/recomp/` and builds `build/game/gitaroo-man`. Rerun
it after changing `config/`; run `vu1lift.py` or `ioplift.py` again (then `ninja -C build/game`) after
changing a lifter. `ninja -C build/game ps2EntryRunner` builds the same game on the runtime's stock
runner, the emulated reference native code is checked against. Without
their output the runtime simply interprets the VU1 microprogram and the IOP modules.

## Running

```sh
build/game/gitaroo-man iso/SLUS_202.94
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

To test a later stage, load a save with the stages unlocked (a completed game's save, copied out
of a PCSX2 memory card with `tools/pcsx2/mcextract.py`) through Options > Load and pick the stage on
the Single Play map. `tools/run/stage.sh` scripts that: it loads bank 1 of the card in
`PS2X_MC_ROOT`, starts stage N and lets the battle play out with no input (it is lost, which still
covers the stage's models, effects and the "Try again?" screen):

```sh
PS2X_MC_ROOT=build/saves/mc0 tools/run/stage.sh 5 st5 900 PS2X_VU1_VERIFY=1
```

`PS2X_INPUT_LIVE=<file>` makes the game also read presses appended to `<file>` while it runs
(`echo "down@0:6,cross@30:6" >> file`, ticks relative to when the line is read); with
`PS2X_DUMP_FRAMES` that is enough to step through menus from another program.

### Useful environment variables

| variable | effect |
|---|---|
| `PS2X_SKIP_MOVIES=1` | skip FMVs |
| `PS2X_INPUT_SCRIPT=...` | scripted pad input (`start@900:8,cross@1400:8`) |
| `PS2X_INPUT_CLOCK=reads` | time scripted input by the game's pad reads instead of vblanks (keeps pace when the game runs slower than real time) |
| `PS2X_INPUT_LIVE=<file>` | also read presses appended to `<file>` while running (ticks relative to when the line is read) |
| `PS2X_DUMP_FRAMES=<dir>`, `PS2X_DUMP_EVERY=N` | write every Nth presented frame as PNG |
| `PS2X_GS_BACKEND=cpu` | draw the GS with the CPU rasterizer instead of OpenGL ([docs/gpu-renderer.md](docs/gpu-renderer.md)) |
| `PS2X_GS_SCALE=N` | internal resolution of the OpenGL renderer (1-8, default 2) |
| `PS2X_GS_GPU_VERIFY=1` | run the CPU rasterizer alongside the GPU one and compare every frame |
| `PS2X_GS_GPU_PRIMLOG=N` / `bad` | with the above and `PS2X_DUMP_FRAMES`: log every draw of frame N (or of the first bad frame) to `primlog.txt` |
| `PS2X_VU1_GPU=1` | transform and light the VU1 batches in the OpenGL vertex shader instead of drawing their packets (opt-in; [docs/native-render.md](docs/native-render.md)) |
| `PS2X_VU1_GPU_VERIFY=1` | with the above: check every such vertex against the packet the VU code produced |
| `PS2X_VU1_GPU_SUBPIXEL=1` | with `PS2X_VU1_GPU=1`: draw those vertices without the GS's 1/16-pixel snapping (for `PS2X_GS_SCALE` above 1) |
| `PS2X_GS_THREADS=N` | CPU rasterizer worker threads (default 6, 0 = inline) |
| `PS2X_MC_ROOT=<dir>` | memory card directory (default `mc0/` next to the ELF); `tools/pcsx2/mcextract.py` copies saves out of a PCSX2 card |
| `PS2X_WIDESCREEN=1` | 16:9: the community widescreen patch (wider 3D view, 2D stretched as in PCSX2), applied at run time |
| `PS2X_ASPECT=W:H` | shape of the displayed picture (default 4:3; with `PS2X_WIDESCREEN=1` also pass `PS2X_ASPECT=16:9` so the window opens wide) |
| `PS2X_WINDOW=WxH` | initial window size (default 960 lines in the display aspect) |
| `PS2X_DEINTERLACE=0` | show raw fields instead of the weaver |
| `GITAROO_AUDIO_DUMP=<file.wav>`, `GITAROO_TRACE_SOUND=1`, `GITAROO_SONG_CLOCK_OFFSET_MS=N` | record the mix; print opens, plays, section and status changes; how far the song clock trails the mix (default: the device buffer) ([docs/native-audio.md](docs/native-audio.md)) |
| `PS2X_AUDIO_LATENCY_MS=N` | on `ps2EntryRunner`: audio buffered ahead of the sound device (default 40; raise if it crackles) ([docs/audio.md](docs/audio.md)) |
| `PS2X_AUDIO_PERIOD_MS=N`, `PS2X_AUDIO_RAYLIB=1` | sound device period (default 5 ms); play through raylib's shared device instead |
| `PS2X_VU1_LIFT=0` | use the VU1 interpreter instead of the recompiled microprogram |
| `PS2X_VU1_NATIVE=0` | run the mesh renderer (entry 0x1B68) as recompiled microcode instead of native C++ |
| `PS2X_VU1_VERIFY=1` | run interpreter and recompiled/native VU1 in lockstep and report any difference |
| `PS2X_RENDER_CENSUS=1` | count AIR.NGP render-context calls (`tools/analysis/render_census.py` names them; [docs/native-render.md](docs/native-render.md)) |
| `PS2X_IOP_LIFT=0`, `PS2X_IOP_LIFT_VERIFY=1`, `PS2X_IOP_LIFT_STATS=1` | interpret the IOP sound drivers; check every compiled block against the interpreter; report coverage ([docs/iop-lifter.md](docs/iop-lifter.md)) |
| `PS2X_VU1_CENSUS=<dir>` | record VU1 programs/entries/jump targets (AC5-format `manifest.json`) |
| `PS2X_NO_EE_INTERP=1` | disable the fallback EE interpreter for runtime-generated code |
| `PS2X_TRACE_GS`, `_VIF`, `_VU1`, `_RPC`, `_PAD`, `_SPU2`, `_TRX`, `_PRESENT`, `_DMACHAIN`, `_IOP_IMPORTS`, `_IOPMEM`, `_MPEG_AUDIO`, `_MC` | subsystem traces |
| `PS2X_TRACE_AUDIO_LATENCY=1`, `PS2X_TRACE_STALLS=1` | audio buffer level and underruns once a second; game-thread stalls (each one is an audio dropout) |
| `PS2X_TRACE_STUB_CHECKS=1`, `PS2X_NO_TIMESLICE=1`, `PS2X_IRQ_CHAIN_STOP=1` | report suspect stub cases (overlapping memcpy, failed allocations, dropped wakeups, IRQ handlers returning 0); A/B switches for EE kernel scheduling semantics (both break movies today) |
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
| `tools/ioplift/` | IOP module (MIPS I) lifter for the sound drivers |
| `tools/pcsx2/` | PCSX2 launcher, PINE client and capture analysis |
| `tools/analysis/` | one-off analysis helpers (IRX disassembler, GS replay, audio comparison, ...) |
| `docs/` | architecture notes, VU1 lifter, PCSX2 reference workflow, research |

Ignored, local-only: `iso/` (your disc), `build/` (all generated output), `logs/`.

## Documentation

- [docs/architecture.md](docs/architecture.md): the game's engine and code layout
- [docs/vu1-lifter.md](docs/vu1-lifter.md): VU1 census, comparison with Ace Combat 5, the lifter and its verification
- [docs/vu1-renderer.md](docs/vu1-renderer.md): what the VU1 mesh renderer computes, and its native C++ replacement
- [docs/gpu-renderer.md](docs/gpu-renderer.md): the OpenGL GS backend, upscaling and its verification
- [docs/native-audio.md](docs/native-audio.md): the game's sound requests, the IMC format and the native sound engine
- [docs/audio.md](docs/audio.md): the emulated audio path (`ps2EntryRunner`), its latency budget and dropouts
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
