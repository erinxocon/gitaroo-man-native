# Notes for Claude and other coding agents

Gitaroo Man (PS2, SLUS-20294) ported to native code by static recompilation. Read README.md for
the build; this file is the working rules and the facts that are expensive to rediscover.

## Hard rules

- **Never commit game material**: no ISO/ELF/IRX/BIOS/save files, memory or VRAM dumps, screenshots,
  PCSX2 captures, recompiled code (`build/recomp`), Ghidra projects/exports (`build/functions.csv`),
  or the generated VU1 file (`external/PS2Recomp/ps2xRuntime/src/lib/vu/generated/`). Only tooling,
  hand-written config, symbol/address tables, docs. Check `git status` before every commit.
- **No AI attribution** in commits or PRs (no `Co-Authored-By`, no "Generated with" lines).
- **Portable runtime code**: plain standard C++20; no intrinsics, inline asm or OS-specific APIs in
  new runtime/generated code unless it is a real blocker (then say so). Dev/profiling scripts may be
  platform-specific. Upstream PS2Recomp uses SSE types for EE registers (sse2neon covers ARM); leave
  that as is.
- **Prove replacements**: anything that replaces interpreted/emulated behaviour needs a reference
  check (lockstep, PCSX2 capture, or before/after dumps). Keep the old path available behind an env var.
- Commit messages: plain, imperative subject; body says why. History is time-ordered; group work in
  theme branches merged with `--no-ff`.

## Layout and workflow

- `external/PS2Recomp` is a submodule (fork `erinxocon/PS2Recomp`, branch `gitaroo-native`). Runtime
  or recompiler changes are committed **inside the submodule** on that branch and pushed, then the
  pointer is bumped in this repo. Keep upstream-worthy fixes separable (one topic per commit).
- `config/gitaroo.toml` drives the recompiler (`stubs`, `native` = force original code even when a
  runtime handler has the same name). `config/symbols/*.txt` are `name address` lines applied to
  Ghidra by `tools/ghidra/export.sh`.
- `src/gitaroo_overrides.cpp`: per-address function overrides compiled into the runner. Keep them
  minimal and document why the original code cannot run.
- Build: `tools/build/recomp.sh` (recompile + build), or `ninja -C build/runtime` after runtime-only
  edits. Editing headers under `external/PS2Recomp/ps2xRuntime/include` rebuilds everything (PCH,
  ~4 min); prefer .cpp-local changes.
- Test runs: `tools/run/run.sh <name> <secs> VAR=...` (log in `logs/`, frames in `build/runs/<name>/`).
  Use scripted input (`tools/run/inputs/*.txt`, e.g. `stage1_battle.txt` reaches the stage 1 battle
  and the loss screen in ~900 s with movies skipped). Look at the dumped frames to check rendering.
- Speed: `tools/run/bench.sh <name>` prints fps per 2 s window; `perf record -p $(pgrep -x ps2EntryRunner)`.

## VU1

- The game has exactly one VU1 microprogram (ELF 0x244480, 1420 pairs). Pair 0x1B70 is a `B` the EE
  patches in-stream to pick one of four render modes (table 0x249360). 6 MSCAL entries; 1B68 is 99.9%
  of VU1 work. Details: docs/vu1-lifter.md, docs/pcsx2-reference.md.
- Entry 1B68 runs as native C++ (`ps2_vu1_native.cpp`, docs/vu1-renderer.md) for drawn and culled
  batches; clipped/extra-pass batches decline and run lifted. It must reproduce memory, packets and
  registers exactly; verify it like the lifter (`PS2X_VU1_NATIVE_BREAK=1` is its negative test).
  `python3 tools/vu1lift/vu1dis.py` disassembles the microprogram.
- `tools/vu1lift/vu1lift.py` generates the recompiled program; the runtime uses it by default when the
  micro image matches. After any change to the lifter or to VU1 semantics, run
  `PS2X_VU1_VERIFY=1` through `stage1_battle.txt` (millions of runs, must report 0 mismatches), and
  `--exact-flags` once if flag handling changed. `--break-move` proves the verifier still catches errors.
- VU sources build with `-ffp-contract=off`: the VU has no FMA. Do not reintroduce contraction.

## GS

- The OpenGL GS backend is the default (`PS2X_GS_BACKEND=cpu` = CPU rasterizer). After changing it,
  run `PS2X_GS_GPU_VERIFY=1` through `stage1_battle.txt` (compares every presented frame with the CPU
  rasterizer; must report 0 bad frames). Verify runs at 1x unless PS2X_GS_SCALE is given: upscaled
  output shows single fields and never matches the CPU's woven frames. See docs/gpu-renderer.md.
- GL work happens on the host (window) thread only; raylib caches GL state, so restore what you change.

## Audio

- The IOP (and the SPU2) only advances while the game thread accounts EE cycles, so anything that
  blocks the game thread -- or the GS host thread long enough to fill its queue -- is an audio
  dropout. Keep slow work (PNG encoding, file writes) off both; `PS2X_TRACE_STALLS=1` finds stalls.
- Output latency matters (rhythm game): `PS2X_TRACE_AUDIO_LATENCY=1` should show ~30-40 ms buffered
  and no underruns through `stage1_battle.txt`. See docs/audio.md.

## Facts worth knowing

- Compiler: Metrowerks CodeWarrior for PS2 (C++, MW-mangled names); the Sony SDK parts (0x120000-
  0x14FFFF) are ee-gcc. gp = 0x0026C470. Engine: iNiS Xeios / AiCube "AIR.NGP" renderer, xg scene graph.
- The game assembles MIPS draw loops at runtime (AiCube vertex-array templates 0x1DA330..0x1DA550);
  the runtime's fallback EE interpreter runs them (`PS2X_NO_EE_INTERP=1` disables).
- Movies: gmMovie (ezmpeg-derived) over an HLE MPEG decoder; audio goes through the game's PCM callback.
- IOP modules run under the IOP emulator: SIO2MAN MTAPMAN MCMAN MCSERV PADMAN LIBSD SDRDRV MSTREAM.
  MSTREAM timing depends on IOP vblank handlers.
- PCSX2 (`tools/pcsx2/`) is the reference for "how it should look"; its GameDB sets eeRoundMode=NegInf
  and patches 0x102DB0.
