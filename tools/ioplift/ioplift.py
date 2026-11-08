#!/usr/bin/env python3
"""Lift Gitaroo Man's IOP sound modules (MSTREAM, SDRDRV, LIBSD) from MIPS I to C++.

The modules otherwise run on the IOP interpreter (ps2xIOP/src/emulator/core/iop_cpu.cpp). Each IRX
is relocated at the address the IOP emulator loads it to, split into basic blocks, and every block
becomes a C++ function with the interpreter's exact semantics: the load delay slot (a loaded value
appears one instruction later and is dropped if that instruction writes the same register), branches
in delay slots ignored, JAL in a delay slot still linking. Instructions that can raise an exception
(overflow, misalignment, SYSCALL/BREAK, unknown encodings) end the block just before themselves so the
interpreter raises it. The runtime only uses a module's blocks when its text in IOP RAM matches the
hash recorded here.

Usage: python3 tools/ioplift/ioplift.py [--iso iso] [-o external/PS2Recomp/ps2xIOP/src/emulator/lifted/generated/iop_lifted_gitaroo.inc]
"""
import argparse
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Module, IRX file, load base. The IOP emulator places Gitaroo's modules deterministically
# (SIO2MAN MTAPMAN MCMAN MCSERV PADMAN LIBSD SDRDRV MSTREAM); the runtime checks the hash anyway.
MODULES = [
    ("LIBSD", "MODULES/LIBSD.IRX", 0x44900),
    ("SDRDRV", "MODULES/SDRDRV.IRX", 0x49200),
    ("MSTREAM", "MODULES/MSTREAM.IRX", 0x4AA00),
]

MAX_BLOCK = 256

R_MIPS_32, R_MIPS_26, R_MIPS_HI16, R_MIPS_LO16 = 2, 4, 5, 6


def u32(v):
    return v & 0xFFFFFFFF


def s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


class Irx:
    def __init__(self, path, base):
        self.base = base
        d = open(path, "rb").read()
        shoff, = struct.unpack_from("<I", d, 0x20)
        shnum, = struct.unpack_from("<H", d, 0x30)
        shstrndx, = struct.unpack_from("<H", d, 0x32)
        secs = [struct.unpack_from("<IIIIIIIIII", d, shoff + i * 40) for i in range(shnum)]
        stroff = secs[shstrndx][4]

        def name(i):
            n = secs[i][0]
            return d[stroff + n:d.index(b"\0", stroff + n)].decode()

        self.sections = {name(i): s for i, s in enumerate(secs)}
        size = 0
        for i, s in enumerate(secs):
            if s[2] & 2:  # SHF_ALLOC
                size = max(size, s[3] + s[5])
        img = bytearray(size)
        for i, s in enumerate(secs):
            if s[2] & 2 and s[1] == 1:  # PROGBITS
                img[s[3]:s[3] + s[5]] = d[s[4]:s[4] + s[5]]
        text = self.sections[".text"]
        self.text_start, self.text_end = text[3], text[3] + text[5]
        iopmod = next(s for s in secs if s[1] == 0x70000080)
        self.entry = struct.unpack_from("<I", d, iopmod[4] + 4)[0] + base
        self.code_pointers = set()

        def word(off):
            return struct.unpack_from("<I", img, off)[0]

        def put(off, v):
            struct.pack_into("<I", img, off, u32(v))

        for i, s in enumerate(secs):
            if s[1] != 9:  # SHT_REL
                continue
            pending_hi = []
            for k in range(s[5] // 8):
                off, info = struct.unpack_from("<II", d, s[4] + k * 8)
                # IRX relocation offsets are module addresses, not section offsets.
                typ = info & 0xFF
                w = word(off)
                if typ == R_MIPS_32:
                    put(off, w + base)
                    if self.text_start <= w < self.text_end:
                        self.code_pointers.add(w + base)
                elif typ == R_MIPS_26:
                    t = ((w & 0x3FFFFFF) << 2) + base
                    put(off, (w & 0xFC000000) | ((t >> 2) & 0x3FFFFFF))
                elif typ == R_MIPS_HI16:
                    pending_hi.append(off)
                elif typ == R_MIPS_LO16:
                    lo = s16(w)
                    for hoff in pending_hi:
                        hw = word(hoff)
                        ahl = ((hw & 0xFFFF) << 16) + lo
                        addr = ahl + base
                        put(hoff, (hw & 0xFFFF0000) | (((addr + 0x8000) >> 16) & 0xFFFF))
                        if self.text_start <= ahl < self.text_end:
                            self.code_pointers.add(u32(addr))
                    if pending_hi:
                        put(off, (w & 0xFFFF0000) | ((lo + base) & 0xFFFF))
                    else:
                        put(off, (w & 0xFFFF0000) | ((w + base) & 0xFFFF))
                    pending_hi = []
                else:
                    sys.exit(f"{path}: unsupported relocation type {typ}")
        self.image = bytes(img)

    def insn(self, addr):
        return struct.unpack_from("<I", self.image, addr - self.base)[0]

    def text_hash(self):
        h = 0xCBF29CE484222325
        for a in range(self.base + self.text_start, self.base + self.text_end, 4):
            h = ((h ^ self.insn(a)) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
        return h


def fields(w):
    return (w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31, (w >> 6) & 31, w & 63, w & 0xFFFF)


def branch_info(pc, w):
    """(kind, static target) for control-transfer instructions, else None. kind: 'b', 'j', 'jr'."""
    op, rs, rt, rd, sa, fn, imm = fields(w)
    if op == 0 and fn in (0x08, 0x09):
        return ("jr", None)
    if op == 1 and rt in (0, 1, 0x10, 0x11):
        return ("b", u32(pc + 4 + (s16(imm) << 2)))
    if op in (2, 3):
        return ("j", ((pc + 4) & 0xF0000000) | ((w & 0x3FFFFFF) << 2))
    if op in (4, 5, 6, 7):
        return ("b", u32(pc + 4 + (s16(imm) << 2)))
    return None


def liftable(w):
    """False for instructions left to the interpreter (they may raise an exception, or are unknown)."""
    op, rs, rt, rd, sa, fn, imm = fields(w)
    if op == 0:
        return fn in (0x00, 0x02, 0x03, 0x04, 0x06, 0x07, 0x08, 0x09, 0x10, 0x11, 0x12, 0x13, 0x18, 0x19,
                      0x1A, 0x1B, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x2A, 0x2B)
    if op == 1:
        return rt in (0, 1, 0x10, 0x11)
    if op == 0x10:
        return rs in (0, 4) or (rs == 0x10 and fn == 0x10)
    return op in (2, 3, 4, 5, 6, 7, 8, 9, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x20, 0x21, 0x22, 0x23, 0x24,
                  0x25, 0x26, 0x28, 0x29, 0x2A, 0x2B, 0x2E)


class Emitter:
    BREAK = False
    """C++ for one instruction, mirroring IopCpuCore::executeInstruction.

    Returns (code, written_reg, load) where load is (reg, expr) for loads (delayed), and a guard
    expression (or None) under which the interpreter must take over before the instruction."""

    def __init__(self):
        pass

    @staticmethod
    def r(n):
        return "0u" if n == 0 else f"g[{n}]"

    def emit(self, pc, w):
        op, rs, rt, rd, sa, fn, imm = fields(w)
        r = self.r
        simm = s16(imm)
        guard = None
        code = []
        written = 0
        load = None

        def wr(reg, expr):
            nonlocal written
            if reg:
                code.append(f"g[{reg}] = {expr};")
                written = reg

        addr = f"({r(rs)} + 0x{u32(simm):x}u)"
        if op == 0:
            if fn == 0x00:
                wr(rd, f"{r(rt)} << {sa}")
            elif fn == 0x02:
                wr(rd, f"{r(rt)} >> {sa}")
            elif fn == 0x03:
                wr(rd, f"(uint32_t)((int32_t){r(rt)} >> {sa})")
            elif fn == 0x04:
                wr(rd, f"{r(rt)} << ({r(rs)} & 31u)")
            elif fn == 0x06:
                wr(rd, f"{r(rt)} >> ({r(rs)} & 31u)")
            elif fn == 0x07:
                wr(rd, f"(uint32_t)((int32_t){r(rt)} >> ({r(rs)} & 31u))")
            elif fn == 0x09:
                wr(rd if rd else 31, f"0x{u32(pc + 8):x}u")
            elif fn == 0x08:
                pass
            elif fn == 0x10:
                wr(rd, "cpu.hi")
            elif fn == 0x11:
                code.append(f"cpu.hi = {r(rs)};")
            elif fn == 0x12:
                wr(rd, "cpu.lo")
            elif fn == 0x13:
                code.append(f"cpu.lo = {r(rs)};")
            elif fn == 0x18:
                code.append(f"{{ const int64_t m = (int64_t)(int32_t){r(rs)} * (int64_t)(int32_t){r(rt)}; "
                            f"cpu.lo = (uint32_t)m; cpu.hi = (uint32_t)((uint64_t)m >> 32); }}")
            elif fn == 0x19:
                code.append(f"{{ const uint64_t m = (uint64_t){r(rs)} * {r(rt)}; cpu.lo = (uint32_t)m; "
                            f"cpu.hi = (uint32_t)(m >> 32); }}")
            elif fn == 0x1A:
                code.append(f"div_s(cpu, {r(rs)}, {r(rt)});")
            elif fn == 0x1B:
                code.append(f"div_u(cpu, {r(rs)}, {r(rt)});")
            elif fn == 0x20:
                guard = f"add_overflows({r(rs)}, {r(rt)})"
                wr(rd, f"{r(rs)} + {r(rt)}")
            elif fn == 0x21:
                wr(rd, f"{r(rs)} + {r(rt)}")
            elif fn == 0x22:
                guard = f"sub_overflows({r(rs)}, {r(rt)})"
                wr(rd, f"{r(rs)} - {r(rt)}")
            elif fn == 0x23:
                wr(rd, f"{r(rs)} - {r(rt)}")
            elif fn == 0x24:
                wr(rd, f"{r(rs)} & {r(rt)}")
            elif fn == 0x25:
                wr(rd, f"{r(rs)} | {r(rt)}")
            elif fn == 0x26:
                wr(rd, f"{r(rs)} ^ {r(rt)}")
            elif fn == 0x27:
                wr(rd, f"~({r(rs)} | {r(rt)})")
            elif fn == 0x2A:
                wr(rd, f"((int32_t){r(rs)} < (int32_t){r(rt)} ? 1u : 0u)")
            elif fn == 0x2B:
                wr(rd, f"({r(rs)} < {r(rt)} ? 1u : 0u)")
        elif op == 1:
            if rt in (0x10, 0x11):
                wr(31, f"0x{u32(pc + 8):x}u")
        elif op == 3:
            wr(31, f"0x{u32(pc + 8):x}u")
        elif op == 8:
            guard = f"add_overflows({r(rs)}, 0x{u32(simm):x}u)"
            wr(rt, f"{r(rs)} + 0x{u32(simm):x}u")
        elif op == 9:
            wr(rt, f"{r(rs)} + 0x{u32(simm + (1 if Emitter.BREAK else 0)):x}u")
        elif op == 0x0A:
            wr(rt, f"((int32_t){r(rs)} < {simm} ? 1u : 0u)")
        elif op == 0x0B:
            wr(rt, f"({r(rs)} < 0x{u32(simm):x}u ? 1u : 0u)")
        elif op == 0x0C:
            wr(rt, f"{r(rs)} & 0x{imm:x}u")
        elif op == 0x0D:
            wr(rt, f"{r(rs)} | 0x{imm:x}u")
        elif op == 0x0E:
            wr(rt, f"{r(rs)} ^ 0x{imm:x}u")
        elif op == 0x0F:
            wr(rt, f"0x{u32(imm << 16):x}u")
        elif op == 0x10:
            if rs == 0:
                load = (rt, f"cpu.cop0[{rd}]")
            elif rs == 4:
                code.append(f"cpu.cop0[{rd}] = {r(rt)};")
            else:
                code.append("cpu.cop0[12] = (cpu.cop0[12] & ~0x0Fu) | ((cpu.cop0[12] >> 2u) & 0x0Fu);")
        elif op in (0x20, 0x24):
            cast = "(uint32_t)(int32_t)(int8_t)" if op == 0x20 else "(uint32_t)"
            load = (rt, f"{cast}mem.read8({addr})")
        elif op in (0x21, 0x25):
            guard = f"({addr} & 1u)"
            cast = "(uint32_t)(int32_t)(int16_t)" if op == 0x21 else "(uint32_t)"
            load = (rt, f"{cast}mem.read16({addr})")
        elif op == 0x22:
            load = (rt, f"lwl_value(mem, {addr}, {r(rt)})")
        elif op == 0x23:
            guard = f"({addr} & 3u)"
            load = (rt, f"mem.read32({addr})")
        elif op == 0x26:
            load = (rt, f"lwr_value(mem, {addr}, {r(rt)})")
        elif op == 0x28:
            code.append(f"mem.write8({addr}, (uint8_t){r(rt)});")
        elif op == 0x29:
            guard = f"({addr} & 1u)"
            code.append(f"mem.write16({addr}, (uint16_t){r(rt)});")
        elif op == 0x2A:
            code.append(f"swl_store(mem, {addr}, {r(rt)});")
        elif op == 0x2B:
            guard = f"({addr} & 3u)"
            code.append(f"mem.write32({addr}, {r(rt)});")
        elif op == 0x2E:
            code.append(f"swr_store(mem, {addr}, {r(rt)});")
        if load and load[0] == 0:
            load = None
        return code, written, load, guard


def branch_cond(w):
    op, rs, rt, rd, sa, fn, imm = fields(w)
    r = Emitter.r
    if op == 1:
        return f"((int32_t){r(rs)} < 0)" if rt in (0, 0x10) else f"((int32_t){r(rs)} >= 0)"
    return {4: f"({r(rs)} == {r(rt)})", 5: f"({r(rs)} != {r(rt)})", 6: f"((int32_t){r(rs)} <= 0)",
            7: f"((int32_t){r(rs)} > 0)"}.get(op)


def lift_module(name, irx):
    start, end = irx.base + irx.text_start, irx.base + irx.text_end
    leaders = {start, irx.entry} | {p for p in irx.code_pointers if start <= p < end}
    delay_slots = set()
    for pc in range(start, end, 4):
        bi = branch_info(pc, irx.insn(pc))
        if bi:
            delay_slots.add(pc + 4)
            leaders.add(pc + 8)
            if bi[1] is not None and start <= bi[1] < end:
                leaders.add(bi[1])
    leaders = sorted(p for p in leaders if start <= p < end)
    leader_set = set(leaders)
    em = Emitter()
    out = []
    names = {}

    def emit_insn(body, pc, w, bail):
        """One non-branch instruction: guard, writes, load-delay step, new load."""
        code, written, load, guard = em.emit(pc, w)
        body.append(f"    // {pc:08x}")
        if guard:
            body.append(f"    if ({guard}) {{ {bail} }}")
        if load:
            # The address (and LWL/LWR's old register) are read before the pending load lands.
            body.append(f"    {{ const uint32_t lv = {load[1]};")
            body.extend("    " + c for c in code)
            body.append(f"    STEP({written}); LOAD({load[0]}, lv); }}")
        else:
            body.extend("    " + c for c in code)
            body.append(f"    STEP({written});")

    for lead in leaders:
        body = []
        pc = lead
        count = 0
        tail = None  # code after the body: sets cpu.pc and returns count
        while True:
            if pc >= end or count >= MAX_BLOCK or (count and pc in leader_set and pc not in delay_slots):
                tail = f"    SYNC_PENDING(); cpu.pc = 0x{pc:x}u; return {count};"
                break
            w = irx.insn(pc)
            bi = branch_info(pc, w)
            if not liftable(w) or (bi and (pc + 4 >= end or not liftable(irx.insn(pc + 4)))):
                # The interpreter runs this one (or this branch and its delay slot).
                tail = f"    SYNC_PENDING(); cpu.pc = 0x{pc:x}u; return {count};"
                break
            if not bi:
                emit_insn(body, pc, w, f"SYNC_PENDING(); cpu.pc = 0x{pc:x}u; return {count};")
                count += 1
                pc += 4
                continue
            kind, target = bi
            ds = pc + 4
            if kind == "jr":
                body.append(f"    const uint32_t tgt = {Emitter.r((w >> 21) & 31)}; const bool taken = true;")
            else:
                cond = branch_cond(w) if kind == "b" else "true"
                body.append(f"    const uint32_t tgt = 0x{target:x}u; const bool taken = {cond};")
            code, written, load, guard = em.emit(pc, w)
            body.append(f"    // {pc:08x}: branch")
            body.extend("    " + c for c in code)
            body.append(f"    STEP({written});")
            count += 1
            # Delay slot; a branch here is ignored but still links.
            emit_insn(body, ds, irx.insn(ds), f"SYNC_PENDING(); cpu.pc = 0x{ds:x}u; cpu.branchPending = taken; "
                                              f"cpu.branchTarget = tgt; return {count};")
            count += 1
            tail = f"    SYNC_PENDING(); cpu.pc = taken ? tgt : 0x{ds + 4:x}u; return {count};"
            break
        if count == 0:
            continue
        fname = f"b_{lead:08x}"
        names[lead] = fname
        out.append(f"template <class M> static uint32_t {fname}(IopCpuState &cpu, M &mem)\n{{")
        out.append("    BLOCK_BEGIN();")
        out.extend(body)
        out.append(tail)
        out.append("}\n")
    return out, names, start, end


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=os.path.join(ROOT, "iso"))
    ap.add_argument("-o", "--output", default=os.path.join(
        ROOT, "external/PS2Recomp/ps2xIOP/src/emulator/lifted/generated/iop_lifted_gitaroo.inc"))
    ap.add_argument("--break", dest="break_", action="store_true",
                    help="negative test: every ADDIU adds one more, which the verifier must report")
    a = ap.parse_args()
    if a.break_:
        Emitter.BREAK = True
    lines = ["// Generated by tools/ioplift/ioplift.py from the game's IRX modules. Do not edit or commit.", ""]
    tables = []
    total = 0
    for name, rel, base in MODULES:
        irx = Irx(os.path.join(a.iso, rel), base)
        body, names, start, end = lift_module(name, irx)
        lines.extend(body)
        total += len(names)
        n = (end - start) // 4
        lines.append(f"template <class M> static const BlockFn<M> kBlocks_{name}[{n}] = {{")
        row = []
        for i in range(n):
            pc = start + i * 4
            row.append(f"&{names[pc]}<M>" if pc in names else "nullptr")
            if len(row) == 8:
                lines.append("    " + ", ".join(row) + ",")
                row = []
        if row:
            lines.append("    " + ", ".join(row) + ",")
        lines.append("};\n")
        tables.append((name, start, end, irx.text_hash()))
        print(f"{name}: base 0x{base:x} text 0x{start:x}-0x{end:x} entry 0x{irx.entry:x} "
              f"{len(names)} blocks, hash {irx.text_hash():016x}")
    lines.append("template <class M> static const LiftedModule<M> kLiftedModules[] = {")
    for name, start, end, h in tables:
        lines.append(f"    {{\"{name}\", 0x{start:x}u, 0x{end:x}u, 0x{h:016x}ull, kBlocks_{name}<M>}},")
    lines.append("};")
    os.makedirs(os.path.dirname(a.output), exist_ok=True)
    with open(a.output, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"{total} blocks -> {a.output}")


if __name__ == "__main__":
    main()
