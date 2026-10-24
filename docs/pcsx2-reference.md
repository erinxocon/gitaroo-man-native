# PCSX2 as a reference

PCSX2 runs the real game, so it is the ground truth for "what is this supposed to look like / do".
The tools here boot your disc in PCSX2 with an IPC socket open, archive save states while you play,
and pull memory, VU1 state and logs out of them. Nothing captured is committed (it is game data);
this page records the workflow and what was learned from it.

## Setup (Linux, PCSX2 flatpak)

- PCSX2 2.8.x flatpak with your own BIOS. In Settings enable *Advanced > PINE* and the EE/IOP
  consoles (the game's `printf` output lands in the log).
- `tools/pcsx2/pcsx2.sh [args]` boots `GITAROOMAN.ISO` from the repo root and writes the app log to
  `logs/pcsx2_<time>.log`. The flatpak's `XDG_RUNTIME_DIR` is a private tmpfs; the script points it at
  `/run/user/$UID/app/net.pcsx2.PCSX2` so the PINE socket is visible to the host. Useful args:
  `-fastboot`, `-statefile <p2s>`, `-debugger`.

## Tools

| command | what it does |
|---|---|
| `tools/pcsx2/pine.py info` | PCSX2 version, status, game id |
| `tools/pcsx2/pine.py read <addr> [len]` | hexdump EE memory |
| `tools/pcsx2/pine.py dump <out> [addr len]` | raw EE RAM (32 MB in ~0.5 s) |
| `tools/pcsx2/pine.py poll <addr>` | print a u32 whenever it changes |
| `tools/pcsx2/pine.py state <dir> [slot]` | save a state through PINE and unpack it |
| `tools/pcsx2/pine.py collect <dir>` | archive + unpack every save state PCSX2 writes (so one F1 slot can be reused forever) |
| `tools/pcsx2/analyze_session.py <log> <dir>` | per-save table: stage/section/FMV from the log, VU1 micro memory vs the ELF, 0x1B70 patch targets |

A `.p2s` is a zip whose members are zstd-compressed (method 93): `eeMemory.bin`, `iopMemory.bin`,
`vu1MicroMem.bin`, `vu1Memory.bin`, `GS.bin`, `SPU2.bin`, `Screenshot.png`, ...

The PCSX2 log includes `microVU1: Cached Prog [PC=xxxx]` lines: one per VU1 *block start* microVU
compiled, i.e. MSCAL entries **and** JR/JALR targets (not just program entries).

## Log landmarks (game console output)

- Boot banner: `<< Welcome to GitarooMan Album v0.8.0 (04.22.2001) >>`, `AIR.NGP May 12 2001 ... starting up`.
- `Single Player Startup Memory usage`, then `Loading model file <NAME>.XG` per stage. Stage keys:
  stage 1 `PUNPAS.XG`, 2 `ST02BG1`, 3 `ST03BG1`, 4 `STREAM01`, 5 `ST05BG1`, 6 `ST06BG01`, 7 `ST07BG*`,
  8 `ST08BG1`, 9 `ST09BG1`, 10 `ST10BG1`.
- `Switched to section: N` (stage 1: 0 intro, 1 charge, 2/3 alternate attack/guard, 4, 5 end).
- `FMV started` / `FMV ended`, `Waiting for preload to end...`, padman `*** VBLANK OVERLAP ***`.

PCSX2 GameDB settings for this game: `eeRoundMode=1` (NegInf, "fixes disappearing characters") and a
patch at 0x102DB0 (`beqz v0,+0x15` -> nop, "fix stage 4 flashing triangles"). Community patches:
widescreen (FOV float at 0x26FC70 plus eight HUD `lui` fixes) and no-interlacing (`201C8B14 = AF808048`).

## What the captures showed (2026-10-23)

Two sessions: the tutorial into the stage 1 battle (10 states), and a full playthrough of stages
2-10 to the credits (57 states).

- **One VU1 microprogram for the whole game**: VU1 micro memory equals the ELF image at 0x244480
  (1420 pairs, uploaded to micro address 0) in every state, except pair 0x1B70. Micro 0x2C60-0x4000
  is never executed.
- **0x1B70 is a render-mode switch**: the EE (code around 0x1BF000) emits `FLUSHE; MPG 1 @0x36E; B`
  with the target from table 0x249360 indexed by `ctx+0x6F4`: 0x1B80, 0x2108, 0x1E10, 0x2280
  (ELF default 0x0000 before the first patch).
- **42 microVU block starts** = 6 MSCAL entries (0668 0720 0768 0788 07d0 1b68) + 36 JR targets,
  identical to what our port's VU1 census records. Entry 1B68 does 99.9% of VU1 work.
- VU0 micro mode is never used.

These facts drive the VU1 lifter; see [vu1-lifter.md](vu1-lifter.md).

## Saves

The port's memory card is a directory (`mc0/` next to the ELF, or `PS2X_MC_ROOT`). To carry a PCSX2
save over: `python3 tools/pcsx2/mcextract.py <Mcd001.ps2>` lists the card,
`--extract BASLUS-20294USER --to <dir>` copies the Gitaroo save out. A completed-game save loads
(the game reads its 4320-byte record), but Single Play still starts at the tutorial; how the game
offers later stages from it is not worked out yet. Saves are personal data and stay out of git.
