# VU1: the Ace Combat 5 approach applied to Gitaroo Man (2026-10-23)

AC5's port (~/repos/ext/ac5, tools/vurecomp) records the VU1 microprograms a game really runs (census), then
lifts each one to C (one goto-block function per program, JR = switch over block starts, unknown
target = fall back to the interpreter), checked in lockstep against the interpreter.

## What was done
- `PS2X_VU1_CENSUS=<dir>` (external/PS2Recomp/ps2xRuntime/src/lib/vu/ps2_vu1_census.cpp): writes AC5's `manifest.json` + `.bin` images
  (AC5's `python -m vurecomp --programs <dir> --stats / -o` runs on it unchanged: 5 images lifted, 60k lines)
  and `gitaroo_census.json`: per-pair execution counts, JR/JALR targets, and timing-hazard counters that
  compare an instruction-counted model (AC5's lifted code) with our cycle model at every Q/P/flag read,
  VI branch-backup reads, and stores into a not-yet-transferred XGKICK packet.
- `tools/vu1lift/census_compare.py [dir]` compares a census with the ELF microprogram, the PCSX2
  logs, and AC5's CFG coverage.
- Runs: tutorial to "Practice Again?" (2.28G pairs) and stage 1 battle to the loss (2.42G pairs).

## Findings
1. One microprogram for the whole game. Our VU1 micro memory always equals the ELF image
   (0x244480, 1420 pairs) except pair 0x1B70, same as all 57+10 PCSX2 states.
2. 0x1B70 is a mode switch, not program identity. EE code at ~0x1BF000 (Ghidra fragments entry_001bef*)
   emits `FLUSHE; MPG 1 @0x36E; B target` when ctx[0xBE0]&0x40, with target = table 0x249360[ctx[0x6F4]]
   = {0x1B80, 0x2108, 0x1E10, 0x2280}. ELF default is 0x0000 (only seen before the first patch).
   Share of pairs: 1b80 67%, 2108 23%, 2280 6%, 1e10 4%. AC5 would see 5 "programs" (5 near-identical
   functions); a Gitaroo lifter should emit one function with a 5-way switch on the live word at 0x1B70.
3. Entries: 6 MSCAL entries (0668 0720 0768 0788 07d0 1b68) + 36 JR/JALR targets = exactly the 42
   "Cached Prog" PCs PCSX2 logged. Entry 1B68 is 99.9% of VU1 work (avg ~230 pairs/call).
4. AC5 CFG from MSCAL entries only covers 92.9% of executed pairs (19 JR targets are not block starts
   -> interpreter fallback); adding the census's JR targets as roots covers 100%.
5. Timing: 341.7M timing-sensitive reads (Q after DIV/RSQRT, clip after CLIP/FCSET) -- 0 disagreements
   between instruction-counted and cycle-counted models. Stalls (15% of our VU1 cycles) never change a
   result, so a lifted path does not need the stall model. No MFP/FMAND reads within latency.
   But delayed flags ARE required: 0x0608 CLIP .. 0x0620 FCSET 0 .. 0x0628 FCGET reads the CLIP
   result because the FCSET write is still in flight (same idiom at 0x2180/0x2188).
6. VI branch backup across JR: JR vi15 at 0x0568 has `ISUBIU vi1,vi5,3` in its delay slot and both
   targets 0x24E0/0x25E8 start with `IBLTZ vi1` -> the branch must see the OLD vi1 (386k times per run).
   AC5's emitter evaluates it with the new value; a lifter must carry the backup through the JR switch.
   Static scan found no other VI-before-branch site.
7. XGKICK: no store ever hits the untransferred part of a kicked packet, so snapshotting the packet
   at XGKICK (AC5's vu_do_xgkick) is equivalent.
8. Side note: our interpreter decodes EFU specials 0x77/0x79/0x7C/0x7D as ERSQRT/ESIN/EATAN/EEXP; per the
   VU encodings (and AC5's decoder) ERSQRT=0x7BD->0x79, ESIN=0x7FC->0x7C, EATAN=0x7FD->0x7D,
   EEXP=0x7FE->0x7E. Gitaroo only uses ERLENG/ESQRT (which agree), so it is harmless here.

## Lifter recipe for this game
Single function over the ELF image; roots = 6 entries + 36 JR targets + 26 BAL returns; 0x1B70 = switch
on the live word; JR = switch over block starts, else return to interpreter; Q/P readiness and MAC/clip
flags counted in instructions (4-slot ring, FCSET delayed); VI backup state kept across branches/JR;
XGKICK snapshot; no stall model; lockstep verify against VU1Interpreter (AC5's PS2_VU_VERIFY pattern).

## Built (2026-10-24): tools/vu1lift + PS2X_VU1_VERIFY
- `python3 tools/vu1lift/vu1lift.py` reads the program from iso/SLUS_202.94 and the roots from
  config/vu1_lift.json, and writes external/PS2Recomp/ps2xRuntime/src/lib/vu/generated/ps2_vu1_lifted_gitaroo.inc (178 blocks,
  1054/1420 pairs reachable, ~14k lines of plain C++; no intrinsics). Rebuild the runtime afterwards.
- external/PS2Recomp/ps2xRuntime/src/lib/vu/ps2_vu1_lifted.{h,cpp}: VU1Interpreter::execute() runs the lifted program when the micro
  image matches (any B target at 0x1B70) and the entry is a lifted block. Upper ops call execUpper();
  lower ops are inlined; Q/P/flag pipelines are the interpreter's, clocked per instruction plus the
  explicit waits; XGKICK sends the whole packet at once; unknown JR/patch targets hand off to the
  interpreter. Default on; `PS2X_VU1_LIFT=0` = interpreter only.
- `PS2X_VU1_VERIFY=1`: every run executes the interpreter and the lifted code from the same snapshot and
  compares VF/VI/ACC/Q/P/I/R/MAC/status/clip/pc, VU1 data memory and every XGKICK packet; the
  interpreter's result is kept. Result: 10.0M runs (tutorial + stage 1 battle), 0 mismatches, 0 handoffs.
  Negative test (`--break-move`, corrupts LQ loads): 971k/983k runs flagged immediately.
  `--break-vi-backup` produced no mismatch: in this game the old and new vi1 at 0x24E0/0x25E8 never
  differ in sign, so the backup never changes a branch (kept anyway, it is the hardware rule).
- Speed (tools/run/bench.sh, tutorial stage): heavy scenes 27-30 fps -> 40-60 fps; a scripted
  stage 1 run reached frame 17.4k in 300 s vs ~12.4k in 900 s before.
- Upper ops inlined (2026-10-24): every FMAC op is plain C++ (normalized operands, exact-result
  fallback only near the float range edges via the interpreter's own functions); CLIP still calls
  execUpper. MAC/status are computed only for the 3 MULx feeding FMAND (backward liveness from every
  flag reader; `--exact-flags` computes all 306). Verified: exact-flags 10.2M runs and elided 6.4M runs,
  0 mismatches. VU sources now build with -ffp-contract=off: the interpreter had been fusing MADD/MSUB
  into FMA under -march=native (single rounding, CPU-dependent results).
- Speed: tutorial bench locked at 60 fps (was 38-60 lifted, 27-30 interpreted); stage 1 battle 30-59 fps.
  Profile of the battle: GS software rasterizer threads 82% of samples, game thread 18% (VU1 ~8%).
  The GS backend is now the bottleneck; a GPU GS keeps all of this (VU1 still produces the packets),
  and AC5-style native mesh rendering would use the lifted program as its fallback and reference.
