# Persona 4 decomp: notes for the Gitaroo Man port

Researched 2026-10-23. Sources:

- Persona 4 repo cloned to a local clone (commit `854e43d`, 2026-10-23).
  Upstream: https://github.com/Raikaru/Persona4-Decompilation
- decomp.me (sparse clone) at a local sparse clone of decomp.me, for the compiler list.
- Read-only measurements of our `iso/SLUS_202.94`. The scripts are in the session scratchpad;
  they are not part of the repo.

"Unverified" marks claims I could not confirm from a primary source.

---

## TL;DR

1. **P4 is a matching decomp.** It is not a port. It rebuilds `SLUS_217.82` byte for byte
   (two SHA-1 checks) from a mix of C and `INCLUDE_ASM` fallbacks.
   - Tools: splat64 0.39.1 (pinned), spimdisasm and rabbitizer, a vendored `mwccgap`
     (asm-processor for MWCC), and decompals binutils (R5900 `as`).
   - Compilers: MWCCPS2 under **wibo**. GNU ld does the link (MWLD is optional).
   - Scoring and diffing are its own Python tools (`tools/verify.py`, `fndiff.py`,
     `permute*.py`). objdiff is only for interactive diffing; decomp.dev publishes progress.
2. **A native PC port is being planned in a separate private repo** (`Raikaru/Persona4-PC`), in
   the style of re3. The plan:
   - Compile the matched C for i386 with clang.
   - mmap the retail image at 0x100000 for data, and patch function-pointer words at load.
   - Replace the PS2 layers at the game's own wrapper level.
   - Swap RenderWare's sky2 driver for OpenGL.
   - Supply per-function PC overrides keyed by address.

   The plan is in `docs/port/README.md`. That design is the closest model for where Gitaroo
   could go.
3. **Our "MW MIPS C Compiler (2.4.1.01)" string does not identify the compiler release.**
   - P4's retail ELF has the *exact same* `.comment` string, and P4 was built with 3.0.1 b210
     (2006).
   - NBA Ballers (2006) also has it, as the producer of every DWARF CU.
   - P4 states outright that the string "is linker provenance and is not C compiler evidence"
     (`config/target.json`, `wiki/The-Retail-Build.md`).

   So 2.4.1.01 is a fixed tool or backend version string shared across many CodeWarrior PS2
   releases. It does not mean "CW PS2 2.4".
4. **Gitaroo was probably built with an older compiler release than P4's b210, with different
   flags.** Evidence:
   - Build stamp `May 12 2001 19:50:07`, module dates 12/2000–04/2001, Sony SDK libraries
     2.1.0–2.1.4.
   - Its game code fills `jr $ra` delay slots (3107 filled vs 433 `nop`). P4's first-party
     code is documented as *unscheduled*.
   - The most likely candidates on decomp.me are `mwcps2-2.4-001213` and
     `mwcps2-2.3.3-000906`, maybe `mwcps2-2.3-991202`. These are inferences; they need
     empirical tests.
5. **Recommended direction:** a hybrid, decomp-assisted recompilation.
   - Keep PS2Recomp output as the per-function fallback, the way P4 uses `INCLUDE_ASM`.
   - Replace functions one at a time with hand-written C/C++ keyed by address.
   - Spend matching effort only where provable equivalence matters.
   - Copy P4's identification tooling: prologue-based compiler census, SDK archive-signature
     naming, `__FILE__`-string TU recovery, gp-relative symbol recovery.

---

## 1. Project approach and toolchain

### Matching, not porting
`README.md`:
> "A work-in-progress matching decompilation ... The goal is readable C that compiles to the
> original machine code. Unfinished functions still use retail assembly so the build can
> reproduce the executable while recovery continues. This is not a PC port."

- Expected SHA-1s: retail ELF `4eeec036...`, loadable image `3d1d3d2b...`.
- Status (README, last committed report):
  - 13,102 canonical function windows; 9,593 byte-identical (73.2%).
  - Atlus first-party: 6,773 of 6,861 C-matched (98.7%).
  - Sony SDK: 152 of 491 C-matched.
  - Other middleware (RenderWare, CRI): 2,668 of 5,750 C-matched.
- Commit messages and docs talk about "lanes", "workers" and "waves", which suggests heavy
  parallel or automated work (unverified).

### Toolchain

| Piece | What P4 uses | Evidence |
|---|---|---|
| ELF split | **splat64[mips]==0.39.1**, spimdisasm 1.40.2, rabbitizer 1.14.3, all pinned exactly because splat output feeds the byte-exact build | `requirements-python.txt`, `config/slus21782.yaml` |
| Split layout | One `code` segment at vram 0x100000. Subsegments are `asm code1` (0x0), `bin data1` (0x42da00), `asm code2` (0x60c850, a shared Sony kernel blob identical to P3 FES), `bin data2`, `bin zero_fill`. Data is kept as opaque `bin`; it is not symbolized by splat. | `config/slus21782.yaml` |
| Function boundaries | `tools/reconcile_function_boundaries.py` builds a canonical map from spimdisasm's control-flow scan, written to `tools/slus21782_functions.json` as address → window size. Every window must be owned by exactly one `// FUN_xxxxxxxx` marker. | tool docstring |
| Fallback asm | `tools/extract_nonmatching_asm.py` / `regenerate_asm.py` write `asm/nonmatchings/<unit>/func_<addr>.s`. Each file is checked to assemble to exactly the window size. Unlabelled functions are emitted as `.word`. | docstrings |
| C + asm in one TU | **`tools/mwccgap/`**: an asm-processor derivative for MWCC (also used by sotn-decomp, sfiii-decomp, Silent Hill 3). `INCLUDE_ASM` / `INCLUDE_RODATA` reserve nop space; after compilation it transplants the assembled bytes, symbols and relocations. `--skip-asm` gives C-only objects for honest scoring. | `tools/mwccgap/README.md`, `include/include_asm.h` |
| Assembler | decompals binutils v0.7 (`mips-ps2-decompals-as`, `-march=r5900 -mabi=eabi -G=0`) paired with **Debian's** objcopy/ld, because decompals' objcopy writes a bad `.symtab sh_info`. | `Dockerfile`, `wiki/Getting-Started.md` |
| Running MWCC on Linux | **wibo** 0.6.13 (decompals), behind shell wrappers named `mwccps2.exe`. These run under `setarch -R` (ASLR off) because *MWCC register allocation depended on process memory layout*: about 50% of compiles of one function flipped register assignment with ASLR on. Docker must use `--security-opt seccomp=unconfined` for `setarch`. | `Dockerfile` comments |
| Compiler source | Not in the repo (proprietary). Mounted from a private dependencies repo at `/opt/p4`. Windows needs `LMGR326B.DLL` so b210 starts without a license server. decomp.me's binaries come from `ghcr.io/decompme/compilers` images (`cromper/compilers/download.py`). | Getting-Started, Dockerfile, decomp.me |
| Compilers | MWCCPS2 **3.0.1 b210 (060308)** at `-O2` for Atlus code. **3.0.1 b119 (040914)** at `-O4,p -inline auto` for RenderWare 3.7. Some leftover RW units use b210 at `-O2,p`. **ee-gcc 2.96** at `-O2 -G0` for CRI, part of the Sony SDK and libc. The choice is per translation unit via `config/compiler_units.txt`, `gcc_units.txt`, `speed_units.txt` and `version_flags.txt`. | `wiki/The-Retail-Build.md` |
| Linker | GNU ld by default, with a generated script that places each object's sections at their retail addresses: gapped owners, literal pools, jump tables, shared literal merge. **MWLDPS2** is optional (`--linker-backend mwld`); it can't place literal pools at retail addresses. | `docs/gnu_linker.md` |
| Verify | `tools/verify.py` compiles whole files only, cuts each marked function out of the object and compares it to retail with relocation fields masked. It then checks relocation targets separately (`WRONG CALLEE`, `WRONG SYMBOL`). | `wiki/How-Matching-Works.md` |
| Diff and search | `fndiff.py`, `fnalign.py` (instruction alignment), `recon_dis.py` and `eedis.py` (EE ops Capstone lacks). `permute.py` and `permute_ast.py` are an in-repo lightweight decomp-permuter. `pragma_sweep.py`, `probe_variants.py`, `micro_codegen.py`. objdiff only via `make objdiff-objects`. m2c drafts were used and scored, then removed. | `wiki/Tools.md`, `docs/matching.md` §"Score the m2c drafts" |
| Progress | decomp.dev, fed by `tools/gen_decomp_report.py` straight from verifier JSON. They deliberately stopped using objdiff-cli for publishing. | Makefile comments |

### gp, `.sdata`, `.rodata`
- `gp = 0x007690f0` (`config/target.json`).
- gp-relative data symbols are named by their immediate (`iGpffff9f90`, `fGpffff8094`) and
  recorded with evidence in `config/symbol_data_addrs.txt`.
- `tools/recover_symbols.py` bootstraps a symbol table for the stripped ELF: for each
  relocation in a byte-matched function, it reads the resolved value out of retail and backs
  out the addend. A symbol is kept only if every reference agrees.
- The declaration form picks the addressing mode (`docs/matching.md` §"Globals and
  addressing"):
  - `extern T DAT_x[];` gives absolute `lui/lw`.
  - A scalar or pointer `extern` gives GPREL16.
  - `#pragma alias` is silently ignored by b210.
- b210's default small-data threshold admits an 8-byte object (`matching.md` around line
  4748). The exact `-sdatathreshold` default is unverified. `-sdatathreshold 0` was among the
  flags tried.
- Data sections (`.rodata .data .sdata .sbss .bss .lit4 .lit8`) are linked only when the
  object's layout can be proven to sit at retail addresses (`tools/build.py`). Otherwise they
  come from the retail `bin`.

### MWCC-specific quirks they documented
These are from `docs/matching.md` (13k lines), `docs/compiler-floors.md` and `docs/STYLE.md`.

- **Prologue signature:** MWCC saves callee-saved `$s` and `$ra` with **`sq`** (128-bit) at
  every level and build tried. ee-gcc 2.96 uses **`sd`**. This is how they split MWCC units
  from GCC units (`config/gcc_units.txt` header).
- **Build signatures:**
  - `movz/movn` from a ternary appear under b74, b119 and b151 (2003–2005). They never appear
    under b198 or b210.
  - "2.4 and 3.0.3 emit them too but save `$ra` with `sq`/hoist the prologue."
  - b151 has a different inverted-branch trampoline.
  - `-O3` fills delay slots differently.
- **Retail P4 first-party code is unscheduled** (`-O2`, delay slots left as `nop`).
  `#pragma schedule on` inside a guarded body is a lint error.
- **`,p` suffix** (`-O2,p`, "optimize for speed"): an alignment `nop` after a filled back-edge
  delay slot, so the next branch target is 8-byte aligned. Only the command line can set it;
  no pragma does.
- b210 accepts **386 pragmas** and silently ignores unknown ones. Validate pragmas with
  `#pragma warn_illpragma on` (`tools/pragma_audit.py`).
- Useful pragmas: `no_branch_likely`, `opt_propagation`, `opt_common_subs`,
  `opt_loop_invariants`, `auto_inline`, `optimization_level`. Avoid:
  - `opt_pointer_analysis on` crashes mwccgap.
  - `opt_generateconditionalassignments on` triggers an internal compiler error.
- **Frame layout:** spilled parameters pack downward from the top of a quadword (`+0xc`,
  `+0x8`, ...). Compiler temporaries and coalesced-copy roots get 16-byte slots at `+0x0`.
  This was read by reverse-engineering b210 itself (`matching.md` §Types and the EE ABI).
- **ABI:** the int and float argument registers have separate counters. `f(void*, float)`
  puts the pointer in `$a0` and the float in `$f12`, and source order controls
  materialization order. MWCC uses the runtime (`__fixsfdi`, `__floatdisf`, `__adddf3`) for
  s64 conversions and doubles. A bare `1.5` literal is a double.
- An `extern` callee makes b210 assume the full clobber set, while a `static` same-TU callee
  lets it keep caller-save registers. **So TU grouping affects codegen.** Recovering the
  original TU boundaries matters, and they do it with `tools/file_strings.py` and
  `tools/tu_audit.py`.
- `asm void f() { ... }` whole-function asm is used for syscalls, VU0 and hardware code.
  Syscall stubs are emitted as `.word` because MWCC's assembler rejects `syscall`
  (`tools/promote_syscall_stubs.py`).

---

## 2. Native port or PS2 ELF only?

**The decomp produces only a matching PS2 ELF.** `docs/port/` documents a planned native port
in a separate private repo, `Raikaru/Persona4-PC`, which pins the decomp as a submodule. Key
decisions (`docs/port/README.md`):

- **Code:** compile the decomp's C with `clang --target=i386 -m32 -malign-double`.
  `-malign-double` reproduces MWCC's 8-byte `long long` alignment. They go 32-bit first
  because data stores 32-bit pointers.
- **Data:** they don't symbolize all data. They **map the retail executable image at its
  original address 0x100000** (`mmap(MAP_FIXED_NOREPLACE)`, measured working on Linux).
  - `D_` symbols are absolute linker addresses.
  - Only function-pointer words are patched to native addresses: 2,398 words, 2,013 distinct
    functions.
  - Data-to-data pointers stay valid unchanged.
- **Platform layer:** replaced at the *game's own wrappers* (`H_Cdvd_*`, `sdk*`, memcard,
  pad), not at the 213 raw SCE functions.
  - `docs/port/platform_surface.md` inventories every SDK call site: 515 calls to 213 SCE
    functions, with archive:member provenance.
  - Each call site gets a proposed PC replacement: `EIntr`/`DIntr` become no-ops,
    `sceSifLoadModule` becomes a stub, `sceOpen` goes to the host file layer, and so on.
- **Renderer:** keep the matched RenderWare core and swap only the sky2 PS2 driver for
  OpenGL. VU0 inline asm (120 first-party functions, 869 asm lines) is rewritten to C/SSE as
  per-address override files.
- **Overrides:** PC replacements for individual functions are override files keyed by the
  `// FUN_` address, spliced over the original definition at build time. The decomp gets no
  `#ifdef P4_PC`.
- **Audio and video:** ADX/AHX decoded in-process, IOP sound commands reimplemented over
  SDL3, Sofdec via pl_mpeg. They note "the PS2's I/O processor modules are not part of the
  executable".
- **Status:** phase 0 (census) only. 243 files (gcc) and 326 (clang) still fail to compile
  for the host, mostly because of `mode(TI)`/`__int128`, `asm void` and VU0 register names.

**What this means for us:** P4's PC route needs ~100% C first. Gitaroo has no decomp, so our
recompilation is the stand-in for the "C" layer. P4's port ideas (overrides keyed by address,
replacing at the game's wrapper layer, a census of the platform surface) carry over directly.

---

## 3. Identifying MWCC code, SDK versions and function boundaries

Techniques in P4 that we can reuse:

| Technique | P4 file | Use for Gitaroo |
|---|---|---|
| Prologue census: `sq` saves mean MWCC, `sd` saves mean ee-gcc | `config/gcc_units.txt`, `docs/matching.md` §mixed-toolchain | Done below, and it already separates SDK/libc from game code |
| SDK archive-signature naming: normalized bytes (relocs masked) plus the window boundary matched against Sony `.a` members; recorded as `name = addr; // archive:libkernl.a:klib.o; size; canonical_sha256` with no SDK bytes | `config/sdk_symbol_provenance.txt` (generator `sdk_match.py`/`promote_sdk_symbols.py` is "shared" and **not in this repo**) | Replace and extend our hand list `config/symbols/sdk.txt` with SDK 2.1.x archives |
| Cross-game name transfer: relocation-identical function bodies from games that shipped symbols. Only demonstrated relocations are masked (j/jal, `%lo` pair, gp offset), and ambiguous small bodies are rejected | `tools/port_rw_names.py`, `docs/sky2/REPORT.md` | Find another unstripped MWCC PS2 game with the same MSL C++ runtime or SDK 2.1, and transfer names |
| DWARF 1 parser for old MW PS2 debug builds | `tools/rw_dwarf.py` | Use if any iNiS or AiCube-engine title (or a Gitaroo prototype/demo) with `.debug` exists (unverified that one does) |
| `__FILE__` assert strings recover original TU names and spans | `tools/file_strings.py` (pending-register `lui/addiu` + gp decoder) | Gitaroo has `array.cpp`, `arraygl.cpp`, `texmanager.cpp`, `texture.cpp`, `xgNode.cpp`, `xeParticle*.cpp`, `yxeParticleEmitterImpl.cpp`, `libscf.c` |
| TU boundary proposals from multiple signals | `tools/tu_audit.py` | For organizing sources and choosing `static` callees |
| gp-relative symbol recovery | `tools/recover_symbols.py`, `config/symbol_data_addrs.txt` | Name gp globals (`gp = 0x0026c470`) |
| Attributing unlabelled windows by nearest scanned neighbour on both sides (never by whole-file span) | `tools/attribute_windows.py` | Splitting game, SDK and MSL regions |
| Syscall stub detection (`addiu $v1,N; syscall; jr ra; nop`) | `tools/promote_syscall_stubs.py` | Ours are already handled by PS2Recomp syscall HLE |
| Catching the wrong function window | `docs/matching.md` §"A function that cannot be matched may have the wrong WINDOW" | Ghidra boundaries are suspect; our `functions.csv` has 27,858 rows, many not true entries |

### Measurements on our ELF (read-only)
- **Layout:** one PT_LOAD at 0x100000, filesz 0x164680, memsz 0x211280 (bss to 0x311280).
  Sections are unnamed. `.comment` = `MW MIPS C Compiler (2.4.1.01)` + `PlayStation2`.
  `.reginfo` gp = **0x0026c470**. This is the same shape as P4's ELF.
- **First `$ra` save per function start** (from `build/functions.csv (Ghidra export)` starts):
  - 0x100000–0x11ffff: game code, `sq` (157 and 149 per 64 KiB bin).
  - 0x120000–0x14ffff: **`sd`** (147, 200 and 83 per bin). This is where
    `config/symbols/sdk.txt` places libgraph, libkernl, newlib malloc/mem\* and similar
    (232/199/99 symbols). The MWCC side starts at the MSL C++ runtime: `__construct_array`
    at 0x11ffa0 and `__nw__FUi`, both of which use `sq`.
  - 0x150000–0x25xxxx: game, xg, AiCube and gmMovie code, all `sq`.

  **Conclusion:** the Sony SDK libs and newlib were built with ee-gcc, and everything else
  with MWCC (C++, given MW-mangled names such as `__dt__Q23std9exceptionFv`). This matches
  P4's mixed-toolchain pattern.
- **SDK library version strings:**
  - `PsIIlibgraph2100`, `PsIIlibdma  2100`, `PsIIlibipu  2100`, `PsIIlibscf  2100`,
    `PsIIlibsdr  2100`
  - `PsIIlibcdvd 2130`, `PsIIlibpad  2130`
  - `PsIIlibmc   2140`, `9PsIIlibkernl2140`

  So this is Runtime Library release **2.1.0–2.1.4**.
- **Build dates:**
  - `May 12 2001` / `19:50:07` (a `__DATE__`/`__TIME__` pair, presumably)
  - `GitarooMan Album v0.8.0 (04.22.2001)`
  - `gmSinglePlyrGame v1.70 (04.22.2001)`, `gmTest v1.00 (12.20.2000)`, and others
  - `Running Xeios version 1.62`
  - `AiCube xeRender for PS2 (c)1999`
- **Delay slots:** `jr $ra` is followed by a filled delay slot 3107 times and by `nop` 433
  times in game code (and 761 vs 197 in the SDK region). Gitaroo's MWCC code is therefore
  **scheduled**, unlike P4's `-O2` first-party code. Flags are probably a higher `-O` level
  or schedule on (inference).
- **Other counts:** branch-likely opcode words, 1,923 in a raw scan of code and data.
  `movz/movn`-shaped words, 448 in a raw scan (includes data noise; not attributed).
- **xg class-name strings** (`xgMaterial`, `xgDagMesh`, `xgToonEdge`, `xgQuatInterpolator`,
  ...) come from the XGM serializer registry. They are useful anchors for naming vtables and
  constructors.

---

## 4. How applicable is this to Gitaroo? (2.4.1.01 vs 3.0.1 b210)

- **"2.4.1.01" is not a release number.** P4 (3.0.1 b210, built ~2008) and NBA Ballers (2006)
  carry the identical `MW MIPS C Compiler (2.4.1.01)` string; Ballers has it in every DWARF CU
  producer field (`docs/sky2/REPORT.md` §0–1, `tools/rw_dwarf.py`). P4 calls it linker
  provenance. Since it also appears as the DWARF producer, it is more likely a frozen MIPS
  backend or tool version string shared by the compiler and linker (inference).
  **It cannot be used to pick a compiler build.**
- **Release lines on decomp.me**, exact IDs from `cromper/cromper/compilers.py` and
  `cromper/compilers/compilers.linux.yaml` in decomp.me as of 2026-10-22:

  | decomp.me id | display name | note |
  |---|---|---|
  | `mwcps2-2.3-991202` | MWCPS2 2.3 (Built 991202) | runs `mwccmips.exe` |
  | `mwcps2-2.3.3-000906` | MWCPS2 2.3.3 (Built 000906) | |
  | `mwcps2-2.4-001213` | MWCPS2 2.4 (Built 001213) | |
  | `mwcps2-3.0-011126` | MWCPS2 3.0 (Built 011126) | was `3.0b22-011126` |
  | `mwcps2-3.0.1-020123` | MWCPS2 3.0.1 (Built 020123) | |
  | `mwcps2-3.0.3-020716` | MWCPS2 3.0.3 (Built 020716) | |
  | `mwcps2-3.0b22-020926`, `-3.0b38-030307`, `-3.0b50-030527`, `-3.0b52-030722` | 3.0bNN | |
  | `mwcps2-3.0.1b44-030325`, `b51-030512`, `b74-030811`, `b75-030916`, `b87-031208`, `b95-040309`, `b103-040528`, `b119-040914`, `b145-050209`, `b151-050317`, `b198-051011`, `b205-051227`, `b210-060308` | 3.0.1bNNN | P4 uses b210 and b119 |

  - Compile command: `wibo mwccps2.exe -c $FLAGS -nostdinc -stderr in.c -o out.o`.
  - PS2 MWCC flag presets in decomp.me only add a `-fp off|single` choice plus the common
    MWCC flags (`cromper/cromper/flags.py: MWCC_PS2_FLAGS`).
  - Whether any decomp.me *preset* exists for Gitaroo or P4 is unverified; presets are
    stored in the DB, not the repo.
- **Which build fits Gitaroo?**
  - The build date is May 2001 and the SDK is 2.1.x. `2.4-001213` (Dec 2000) is the newest
    public build that predates the stamp; `2.3.3-000906` and `2.3-991202` are older.
    3.0-011126 is dated after the stamp.
  - Caveat: the US release (Feb 2002) could have been rebuilt later, but every date string
    found is ≤ May 2001.
  - P4's cross-build notes say 2.4 saves `$ra` with `sq` and emits `movz` (consistent with
    our 448 movz-shaped words, unconfirmed).
  - **This needs an empirical test.** Take ~10 small, self-contained Gitaroo game functions
    (getters, loops, a switch, a float function, a C++ ctor/dtor). Run them as decomp.me
    scratches (or locally under wibo) against 2.3, 2.3.3, 2.4, 3.0 and 3.0.1, at `-O2`,
    `-O3`, `-O4`, `,p` and schedule on/off. Then compare prologue order, delay-slot filling
    and branch-likely use.
- **Can a matching decomp of Gitaroo use P4's toolchain?** The *infrastructure* yes; the
  *compiler build* probably not.
  - Reusable as is: splat64, mwccgap, decompals as, wibo, the GNU ld placement approach,
    verify.py-style masked compare, the census tools, the per-unit compiler map.
  - Must change:
    - The compiler is likely 2.3.x/2.4.
    - Gitaroo is **C++** (MSL C++ runtime, MW mangling, vtables, `xg*` classes), so the
      matching rules differ from P4's C. P4 tried `-lang c++` only as a probe.
    - Gitaroo's code is scheduled, so P4's b210 playbook (`docs/matching.md`) transfers only
      partly. The general lessons do transfer: whole-file compiles, static callees, typed
      access one field at a time, pragma validation.
  - Scale: Gitaroo's code is about 1.4 MB vs P4's ~4.3 MB code1. Ghidra finds ~5,180
    functions.
- **Getting an older compiler:** decomp.me hosts 2.3, 2.3.3 and 2.4 binaries
  (`ghcr.io/decompme/compilers`). Licensing is unverified; P4 treats the compilers as
  non-redistributable and keeps them in a private repo. Other copies would be old CodeWarrior
  for PS2 SDK CDs (unverified). The "CodeWarrior for PS2 Release N" naming and how it maps
  to these version numbers is unverified; web searches found no primary source.

---

## 5. Recommendations for the Gitaroo port

### A. Adopt a hybrid "decomp-assisted recompilation" (P4 port model, our recomp as the fallback)
1. **PS2Recomp output plays the role of `INCLUDE_ASM`.** Every function is recompiled C++ by
   default, and a function can be replaced by hand-written native code keyed by its address.
   This is how `src/gitaroo_overrides.cpp` and the `native = [...]` list already work. Turn
   that into a structured tree like P4's planned `Persona4-PC` overrides:
   - one file per original TU or module under `src/decomp/<module>/`;
   - a `// FUN_xxxxxxxx` marker on each function;
   - a manifest that the recompiler reads to skip or route those addresses.
2. **Keep the retail image mapped at 0x100000 for data**, as P4's port plan does (our runtime
   already emulates EE RAM). Hand-written functions can read and write globals at their
   original addresses through generated `D_xxxxxxxx`/gp accessors, so full data
   symbolization is unnecessary. Patching function pointers is unnecessary for us too:
   dispatch through the recompiled function table already handles guest addresses.
3. **Replace from the outside in, at the game's own wrapper layer, like P4's plan.** Targets:
   - CD/file wrappers, pad, memcard, sound command senders to MSTREAM/SDRDRV;
   - the gmMovie player (decode to pl_mpeg/ffmpeg instead of IPU HLE);
   - the AiCube renderer entry points (`AirNgp_DrawElements*/DrawArrays`, the VU1 packet
     builders).

   This is also the route to an AC5-style native renderer: intercept at the AiCube/xg API,
   above GIF/VIF.
4. **Make an inventory first**, like `docs/port/platform_surface.md`: every SDK function with
   its call sites and a planned replacement. Our `config/symbols/sdk.txt` (539) plus the prologue
   split gives the starting set.

### B. Matching is optional. Use it where it pays.
- Byte-matching is not required for a native port. It does prove that a hand decompilation is
  correct, which helps for the bulk game logic (judgement, scoring, state machines).
- A cheap middle ground: compile candidate C/C++ with the guessed MWCC build and compare
  against retail with masked relocations, using a small verify.py-style script. A function
  that matches can replace its recompiled version with high confidence. A function that
  doesn't still gets an equivalence check from the existing differential tests (our trace or
  RAM-dump tools versus the recompiled build).

### C. Tooling to adopt (in order of value)
1. **Compiler census script:** port P4's `sq`/`sd` prologue, delay-slot and branch-likely
   scans to tag each function as game (MWCC), SDK/newlib (ee-gcc), or MSL (MWCC). This is
   partly done above.
2. **SDK signature matching** against Sony Runtime Library 2.1.x `.a` archives (libgraph 2100,
   libdma 2100, libpad 2130, libmc 2140, libkernl 2140, libcdvd 2130, libipu 2100, libsdr
   2100, libscf 2100), using P4's normalized hash scheme and recording only names and hashes.
   - P4's generator (`sdk_match.py`) is not public in this repo. Re-implementing it is simple:
     mask the `jal` target, the `%hi/%lo` pair and gp offsets, then hash.
   - newlib (ee-gcc) and the MSL C++ runtime can be matched the same way.
3. **Canonical function map:**
   - Clean `build/functions.csv (Ghidra export)`: 27,858 rows, most of which are not real function starts
     under a prologue test.
   - Build a P4-style `functions.json` (start → size) from spimdisasm or splat, reconciled
     with Ghidra and the vtable-recovered functions.
   - splat64 with `platform: ps2` and a single segment at vram 0x100000 works for this layout
     (same shape as P4's yaml). Keep data as `bin` at first.
4. **`__FILE__`-string TU recovery** (`file_strings.py` approach) for the 10 source filenames
   in our ELF. Also use the RTTI/xg class-name registry strings to name C++ classes and vtables.
5. **gp-relative symbol naming:** `iGpXXXX` = `0x0026c470 + sext(imm)`, recorded with evidence.
6. **Compiler identification experiment** (section 4) before any matching work. If a build
   matches, set up wibo + mwccgap + decompals `as` exactly as P4's Dockerfile does, including
   the `setarch -R` ASLR fix.

### D. Suggested repo organization (from P4's structure)
```
config/
  functions.json        # canonical start->size windows
  symbol_addrs.txt      # name = addr; // type:func  (+ evidence)
  symbol_data_addrs.txt # gp/abs data with evidence
  sdk_provenance.txt    # archive:member + hash, no SDK bytes
  units.txt             # TU -> compiler (mwcc-2.4 / ee-gcc / native-only)
src/decomp/<module>/*.cpp   # hand-decompiled, // FUN_ markers, replaces recomp
src/native/<layer>/         # PC replacements (file, pad, audio, movie, renderer)
docs/research/              # measurements and floors, like P4's docs/ and probe_archive/
```
Follow P4's rules: names need evidence, no `param_N` residue, record measurements, and treat
markers as a tripwire so a function cannot silently vanish from coverage.

---

## Key evidence paths (P4 clone)
- `README.md`, `wiki/Getting-Started.md`, `wiki/The-Retail-Build.md`, `wiki/How-Matching-Works.md`, `wiki/Tools.md`, `wiki/Compiler-Floors.md`
- `config/target.json`: compiler id, gp, and the "2.4.1.01 is linker provenance" statement
- `config/slus21782.yaml` (splat), `config/compiler_units.txt`, `config/gcc_units.txt`, `config/sdk_symbol_provenance.txt`
- `Dockerfile`: wibo, decompals, the ASLR note
- `docs/matching.md` (MWCC playbook), `docs/compiler-floors.md`, `docs/gnu_linker.md`, `docs/STYLE.md`
- `docs/port/README.md`, `platform_surface.md`, `host_build_census.md`, `renderer.md`: the PC port plan
- `docs/sky2/REPORT.md`, `tools/rw_dwarf.py`, `tools/port_rw_names.py`: Ballers DWARF, 2.4.1.01 as producer
- `tools/mwccgap/`, `tools/verify.py`, `tools/file_strings.py`, `tools/tu_audit.py`, `tools/recover_symbols.py`, `tools/reconcile_function_boundaries.py`
- decomp.me: `decomp.me/cromper/cromper/compilers.py` (lines ~726–860), `cromper/compilers/compilers.linux.yaml`, `frontend/src/lib/i18n/locales/en/compilers.json`
