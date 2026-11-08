# IOP sound modules as C++

The IOP (the PS2's I/O processor, an R3000A) runs Sony's and iNiS's sound drivers: LIBSD (the
SPU2 library), SDRDRV (its RPC server for the EE's `sceSdRemote`) and MSTREAM (iNiS's streaming
engine: music, sound effects, and the song clock the battle judges against, see
[audio.md](audio.md)). Everything those modules call (threads, semaphores, interrupts, SIF, CD,
vblank) is already C++ in the IOP emulator (`external/PS2Recomp/ps2xIOP`); only the modules' own
code ran on the interpreter. `tools/ioplift/ioplift.py` compiles it to C++.

## How

- The IRX files are relocated at the addresses the emulator loads them to (LIBSD 0x44900,
  SDRDRV 0x49200, MSTREAM 0x4AA00: the load order and allocator are deterministic). IRX
  relocation offsets are module addresses, not section offsets.
- Block starts: the text start, the module entry, every branch/jump target, the instruction after
  every delay slot (call returns), and every code address in a relocation (function pointers,
  thread entries and handlers passed to the kernel). 2121 blocks for about 10,900 instructions.
- Each block is a C++ function with the interpreter's semantics (`IopCpuCore::executeInstruction`):
  - the load delay slot (a loaded value lands after the next instruction, unless that instruction
    wrote the same register), kept in locals the compiler folds away;
  - a branch in a delay slot is ignored, JAL there still links;
  - an instruction that may raise an exception (ADD/ADDI/SUB overflow, misaligned access) checks
    first and hands that instruction to the interpreter, as do SYSCALL/BREAK and anything unknown.
- A block returns to the emulator's step loop after its basic block, so import stubs, interrupt
  checks and DMA scheduling stay exactly where they were (interrupts are taken at block boundaries
  instead of between any two instructions).
- A module's blocks are used only while its text in IOP RAM hashes to the image they were made
  from (checked when a module is loaded or unloaded).

The output (`ps2xIOP/src/emulator/lifted/generated/iop_lifted_gitaroo.inc`) is derived from the
game and not committed; without it the IOP runs interpreted as before.

## Checking it

- `PS2X_IOP_LIFT_VERIFY=1`: for every block, a dry run (stores held back) gives its instruction
  count; the interpreter runs those instructions for real while IOP memory journals every
  top-level read and store (instruction fetches excluded); the block then runs again from the saved
  state, fed the recorded reads in order, and its registers, pending load, branch state and stores
  must equal the interpreter's. Hardware is only touched once, by the interpreter.
- Results: boot, menus, intro movie and the stage 1 battle: 0 mismatches (millions of blocks).
- `python3 tools/ioplift/ioplift.py --break` (every ADDIU adds one more) is caught on the first
  blocks that run.
- `PS2X_IOP_LIFT_STATS=1` reports how much module code still runs interpreted and where: 0 through
  the stage 1 battle.
- `PS2X_IOP_LIFT=0` runs the modules on the interpreter.
