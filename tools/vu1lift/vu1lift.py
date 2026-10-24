#!/usr/bin/env python3
"""Lift Gitaroo Man's VU1 microprogram (the AIR.NGP renderer) to C++.

The approach follows the Ace Combat 5 port's tools/vurecomp: decode the microcode, build a CFG from
the census roots, and emit one function made of goto blocks, with JR/JALR compiled to a switch over
block starts and anything unknown handed back to the interpreter. Differences, all driven by the
census (docs/vu1-lifter.md):
  - one program, not one per image: the pair the EE rewrites in-stream (0x1B70) is compiled as a
    switch on the live branch word ("dynamic_branches" in the config);
  - the VI branch backup (a branch reads the pre-write value of a VI written by the pair right
    before it) is tracked across every branch and JR, not just straight-line code;
  - semantics come from our VU1Interpreter: upper ops call execUpper(), the Q/P/flag pipelines are
    the interpreter's, clocked once per instruction plus the explicit waits (WAITQ/WAITP, busy
    FDIV/EFU). Data-hazard stalls are dropped: the census measured that they never change a result.

Usage: python3 tools/vu1lift/vu1lift.py [--iso iso/SLUS_202.94] [--config config/vu1_lift.json]
                                        [-o external/PS2Recomp/ps2xRuntime/src/lib/vu/generated/ps2_vu1_lifted_gitaroo.inc]
"""
import argparse
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PC_MASK = 0x3FF8

BRANCHES = {0x20: "B", 0x21: "BAL", 0x28: "IBEQ", 0x29: "IBNE", 0x2C: "IBLTZ", 0x2D: "IBGTZ",
            0x2E: "IBLEZ", 0x2F: "IBGEZ"}
JUMPS = {0x24: "JR", 0x25: "JALR"}
EFU_SPECIALS = set(range(0x70, 0x7E)) - {0x7B}
LANES = "xyzw"


def sext(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


class Pair:
    def __init__(self, pc, lo, up):
        self.pc, self.lo, self.up = pc, lo, up
        self.ibit = bool(up & 0x80000000)
        self.ebit = bool(up & 0x40000000)
        self.mbit = bool(up & 0x20000000)
        self.dbit = bool(up & 0x10000000)
        self.tbit = bool(up & 0x08000000)
        self.uw = up & 0x07FFFFFF
        self.op = None if self.ibit else (lo >> 25) & 0x7F
        self.special = None
        if self.op == 0x40 and (lo & 0x3F) >= 0x3C:
            self.special = (lo & 3) | ((lo >> 4) & 0x7C)

    # ---- lower classification -------------------------------------------------------------
    @property
    def is_branch(self):
        return self.op in BRANCHES

    @property
    def is_jump(self):
        return self.op in JUMPS

    @property
    def branch_target(self):
        return (self.pc + 8 + sext(self.lo & 0x7FF, 11) * 8) & PC_MASK

    def lower_nop(self):
        if self.ibit:
            return True
        if self.lo in (0x00000000, 0x8000033C):
            return True
        return self.special == 0x30 and ((self.lo >> 21) & 0xF) == 0  # MOVE with an empty mask

    # ---- upper classification -------------------------------------------------------------
    def upper_nop(self):
        f = self.uw & 0x3F
        return (f & 0x3C) == 0x3C and ((self.uw & 3) | ((self.uw >> 4) & 0x7C)) in (0x2F, 0x30)

    def upper_vf_write(self):
        """VF register the upper op writes (0 = none), as decodeUpperUsage sees it."""
        if self.upper_nop():
            return 0
        dest = (self.uw >> 21) & 0xF
        if not dest:
            return 0
        f = self.uw & 0x3F
        if (f & 0x3C) == 0x3C:
            sub = (self.uw & 3) | ((self.uw >> 4) & 0x7C)
            if 0x10 <= sub <= 0x17 or sub == 0x1D:  # ITOF/FTOI/ABS write ft
                return (self.uw >> 16) & 0x1F
            return 0                                # ACC ops, CLIP
        return (self.uw >> 6) & 0x1F

    def upper_op(self):
        """(name, kind, operand, to_acc) for the FMAC ops the lifter inlines, else None.
        kind: add sub mul madd msub max mini opmsub itofN ftoiN abs; operand: bc0-3, q, i, vec."""
        f = self.uw & 0x3F
        bcs = ("bc0", "bc1", "bc2", "bc3")
        main = {0x1C: ("mul", "q"), 0x1D: ("max", "i"), 0x1E: ("mul", "i"), 0x1F: ("mini", "i"),
                0x20: ("add", "q"), 0x21: ("madd", "q"), 0x22: ("add", "i"), 0x23: ("madd", "i"),
                0x24: ("sub", "q"), 0x25: ("msub", "q"), 0x26: ("sub", "i"), 0x27: ("msub", "i"),
                0x28: ("add", "vec"), 0x29: ("madd", "vec"), 0x2A: ("mul", "vec"), 0x2B: ("max", "vec"),
                0x2C: ("sub", "vec"), 0x2D: ("msub", "vec"), 0x2E: ("opmsub", "vec"), 0x2F: ("mini", "vec")}
        if (f & 0x3C) != 0x3C:
            if f < 0x1C:
                kind = ("add", "sub", "madd", "msub", "max", "mini", "mul")[f >> 2]
                return (kind, bcs[f & 3], False)
            if f in main:
                return main[f] + (False,)
            return None
        sub = (self.uw & 3) | ((self.uw >> 4) & 0x7C)
        if sub < 0x10:
            return (("add", "sub", "madd", "msub")[sub >> 2], bcs[sub & 3], True)
        if sub < 0x14:
            return ("itof%d" % (0, 4, 12, 15)[sub & 3], "vec", False)
        if sub < 0x18:
            return ("ftoi%d" % (0, 4, 12, 15)[sub & 3], "vec", False)
        if sub < 0x1C:
            return ("mul", bcs[sub & 3], True)
        acc = {0x1C: ("mul", "q"), 0x1E: ("mul", "i"), 0x20: ("add", "q"), 0x21: ("madd", "q"),
               0x22: ("add", "i"), 0x23: ("madd", "i"), 0x24: ("sub", "q"), 0x25: ("msub", "q"),
               0x26: ("sub", "i"), 0x27: ("msub", "i"), 0x28: ("add", "vec"), 0x29: ("madd", "vec"),
               0x2A: ("mul", "vec"), 0x2C: ("sub", "vec"), 0x2D: ("msub", "vec"), 0x2E: ("opmul", "vec")}
        if sub == 0x1D:
            return ("abs", "vec", False)
        if sub in acc:
            return acc[sub] + (True,)
        return None  # CLIP, NOP

    def writes_mac(self):
        """The interpreter queues MAC/status for this upper op (applyFmacDest with a non-empty mask)."""
        op = self.upper_op()
        return bool(op) and op[0] in ("add", "sub", "mul", "madd", "msub", "opmsub", "opmul") \
            and ((self.uw >> 21) & 0xF) != 0

    def reads_mac(self):
        return not self.ibit and self.op in (0x14, 0x16, 0x17, 0x18, 0x1A, 0x1B)

    def lower_vf_regs(self):
        """VF registers the lower op reads or writes (for the same-register pair rule)."""
        if self.lower_nop():
            return set()
        ft, fs = (self.lo >> 16) & 0x1F, (self.lo >> 11) & 0x1F
        if self.op in (0x00,):           # LQ
            return {ft}
        if self.op in (0x01,):           # SQ
            return {fs}
        if self.special is None:
            return set()
        if self.special in (0x30, 0x31, 0x34, 0x35, 0x36, 0x37):  # MOVE MR32 LQI SQI LQD SQD
            return {ft, fs} if self.special in (0x30, 0x31) else ({ft} if self.special in (0x34, 0x36) else {fs})
        if self.special in (0x38, 0x3A):  # DIV RSQRT
            return {fs, ft}
        if self.special in (0x39,):       # SQRT
            return {ft}
        if self.special in (0x3C, 0x42, 0x43) or self.special in EFU_SPECIALS:
            return {fs}
        if self.special in (0x3D, 0x40, 0x41, 0x64):  # MFIR RNEXT RGET MFP
            return {ft}
        return set()


def load_image(iso, va, pairs):
    d = open(iso, "rb").read()
    ph = struct.unpack_from("<I", d, 0x1C)[0]
    for i in range(struct.unpack_from("<H", d, 0x2C)[0]):
        t, off, seg_va, _, fs, _ = struct.unpack_from("<6I", d, ph + i * 32)
        if t == 1 and seg_va <= va < seg_va + fs:
            return d[off + va - seg_va:off + va - seg_va + pairs * 8]
    raise SystemExit("VA %#x not in any PT_LOAD segment of %s" % (va, iso))


class Lifter:
    def __init__(self, image, cfg):
        self.image = image
        self.code = {}
        for pc in range(0, len(image), 8):
            lo, up = struct.unpack_from("<II", image, pc)
            self.code[pc] = Pair(pc, lo, up)
        self.entries = [int(x, 16) for x in cfg["entries"]]
        self.jr_targets = [int(x, 16) for x in cfg["jr_targets"]]
        self.dynamic = {int(k, 16): [int(x, 16) for x in v] for k, v in cfg["dynamic_branches"].items()}
        self.check()
        self.build_cfg()

    def check(self):
        for pc, p in self.code.items():
            if p.dbit or p.tbit:
                raise SystemExit("D/T bit at %04X: not supported by the lifter" % pc)
            nxt = self.code.get(pc + 8)
            if (p.is_branch or p.is_jump) and nxt and (nxt.is_branch or nxt.is_jump):
                raise SystemExit("branch in a delay slot at %04X" % (pc + 8))
            if (p.is_branch or p.is_jump) and (p.ebit or (nxt and nxt.ebit)):
                raise SystemExit("E bit on/after a branch at %04X" % pc)
            if p.ebit and nxt and (nxt.is_branch or nxt.is_jump):
                raise SystemExit("branch in an E-bit delay slot at %04X" % (pc + 8))
            if (p.uw >> 21) & 0xF and not p.upper_nop() and p.upper_vf_write() == 0 and self.writes_vf0(p):
                raise SystemExit("upper op at %04X writes VF0: not supported by the lifter" % pc)
        for pc in self.dynamic:
            if not self.code[pc].op == 0x20:
                raise SystemExit("dynamic branch %04X is not a B" % pc)

    @staticmethod
    def writes_vf0(p):
        f = p.uw & 0x3F
        if (f & 0x3C) == 0x3C:
            sub = (p.uw & 3) | ((p.uw >> 4) & 0x7C)
            return (0x10 <= sub <= 0x17 or sub == 0x1D) and ((p.uw >> 16) & 0x1F) == 0
        return ((p.uw >> 6) & 0x1F) == 0

    def successors(self, pc):
        p = self.code[pc]
        if pc in self.dynamic:
            return list(self.dynamic[pc])
        if p.is_jump:
            return []
        if p.is_branch:
            out = [p.branch_target]
            if p.op not in (0x20, 0x21):
                out.append(pc + 16)
            return out
        return []

    def build_cfg(self):
        roots = set(self.entries) | set(self.jr_targets)
        for targets in self.dynamic.values():
            roots |= set(targets)
        bal_returns = {pc + 16 for pc, p in self.code.items() if p.op == 0x21 or p.op == 0x25}
        roots |= {r for r in bal_returns if r in self.code}
        leaders = set(roots)
        for pc, p in self.code.items():
            if p.is_branch or p.is_jump or pc in self.dynamic:
                leaders.update(s for s in self.successors(pc) if s in self.code)
                leaders.add(pc + 16)
            if p.ebit:
                leaders.add(pc + 16)
        self.leaders = {l for l in leaders if l in self.code}
        self.blocks = {}
        work = sorted(roots & set(self.code))
        while work:
            start = work.pop()
            if start in self.blocks:
                continue
            pairs, succ, kind = [], [], "fall"
            pc = start
            while pc in self.code:
                p = self.code[pc]
                pairs.append(pc)
                if p.ebit:
                    pairs.append(pc + 8)
                    kind = "end"
                    break
                if p.is_branch or p.is_jump or pc in self.dynamic:
                    pairs.append(pc + 8)
                    kind = "jump" if p.is_jump else "branch"
                    succ = self.successors(pc)
                    break
                if pc + 8 in self.leaders:
                    succ = [pc + 8]
                    break
                pc += 8
            else:
                kind = "off"
            self.blocks[start] = (pairs, succ, kind)
            for s in succ:
                if s in self.code and s not in self.blocks:
                    work.append(s)
        self.roots = roots

    def mac_liveness(self, exact):
        """Upper ops whose MAC/status can be read. A reader sees the newest producer issued at least
        four instructions earlier (older ones are overwritten by it), and possibly any producer in the
        three instructions before it (explicit waits can let those commit early). Walk back from
        every reader; give up (everything live) at pairs with unknown predecessors."""
        producers = {pc for pc, p in self.code.items() if p.writes_mac()}
        readers = [pc for pc, p in self.code.items() if p.reads_mac()]
        if exact:
            return producers, True
        preds = {pc: set() for pc in self.code}
        unknown = set(self.roots)
        for pc, p in self.code.items():
            prev = self.code.get(pc - 16)
            if pc - 8 in self.code and not (prev and (prev.op in (0x20, 0x21) or prev.is_jump or prev.ebit)):
                preds[pc].add(pc - 8)
            if p.is_branch or pc in self.dynamic:
                for t in self.successors(pc):
                    if t in preds:
                        preds[t].add(pc + 8)
        live = set()
        for r in readers:
            work, seen = [(q, 1) for q in preds[r]], set()
            if r in unknown:
                return producers, True
            while work:
                pc, d = work.pop()
                if (pc, d) in seen:
                    continue
                seen.add((pc, d))
                if d > 64 or pc in unknown and not (pc in producers and d >= 4):
                    return producers, True
                if pc in producers:
                    live.add(pc)
                    if d >= 4:
                        continue
                work.extend((q, d + 1) for q in preds[pc])
        return live, False

    # ---- emission -------------------------------------------------------------------------
    def emit(self, exact_flags=False):
        self.mac_live, self.flags_exact = self.mac_liveness(exact_flags)
        out = []
        w = out.append
        reach = sorted({pc for b in self.blocks.values() for pc in b[0]})
        w("// Generated by tools/vu1lift/vu1lift.py -- do not edit.")
        w("// Gitaroo Man VU1 microprogram (ELF 0x244480, %d pairs): %d blocks, %d of %d pairs reachable."
          % (len(self.code), len(self.blocks), len(reach), len(self.code)))
        w("// Included at file scope by ps2_vu1_lifted.cpp, after the VU1Lifted helpers.")
        w("")
        w("constexpr uint32_t kImageBytes = 0x%X;" % len(self.image))
        w("alignas(16) static const uint32_t kImage[] = {")
        words = struct.unpack("<%dI" % (len(self.image) // 4), self.image)
        for i in range(0, len(words), 8):
            w("    " + " ".join("0x%08Xu," % x for x in words[i:i + 8]))
        w("};")
        w("static const uint16_t kDynamicPairs[] = {%s};" % ", ".join("0x%04X" % pc for pc in sorted(self.dynamic)))
        w("// MAC/status are computed for %d of %d FMAC ops (the rest cannot be observed by FMAND & co.)."
          % (len(self.mac_live), sum(1 for p in self.code.values() if p.writes_mac())))
        w("constexpr bool kLiftedFlagsExact = %s;" % ("true" if self.flags_exact else "false"))
        w("")
        w("static bool isBlockStart(uint32_t pc)")
        w("{")
        w("    switch (pc)")
        w("    {")
        for b in sorted(self.blocks):
            w("    case 0x%04X:" % b)
        w("        return true;")
        w("    default:")
        w("        return false;")
        w("    }")
        w("}")
        w("")
        w("int VU1Lifted::runProgram(VU1Interpreter &vu, LiftedRun &run, uint32_t startPc)")
        w("{")
        w("    BranchBackup bk{};")
        w("    uint8_t *const mem = run.mem;")
        w("    const uint32_t dm = run.dataMask;")
        w("    (void)mem;")
        w("    (void)dm;")
        w("    switch (startPc)")
        w("    {")
        for b in sorted(self.blocks):
            w("    case 0x%04X: goto B_%04X;" % (b, b))
        w("    default: return kLiftNotLifted;")
        w("    }")
        for start in sorted(self.blocks):
            self.emit_block(start, w)
        w("}")
        return "\n".join(out) + "\n"

    def go(self, target, indent="    "):
        if target in self.blocks:
            return "%sgoto B_%04X;" % (indent, target)
        return "%sreturn handoff(vu, bk, 0x%04Xu);" % (indent, target)

    def emit_block(self, start, w):
        pairs, succ, kind = self.blocks[start]
        w("")
        w("B_%04X:" % start)
        w("    if (vu.m_cycle >= run.budgetEnd)")
        w("        return yield(vu, bk, 0x%04Xu);" % start)
        if kind in ("fall", "off"):
            for pc in pairs:
                self.emit_pair(pc, w)
            nxt = pairs[-1] + 8
            w(self.go(nxt))
            return
        if kind == "end":
            for pc in pairs:
                self.emit_pair(pc, w)
            w("    vu.m_state.pc = 0x%04Xu;" % ((pairs[-1] + 8) & PC_MASK))
            w("    return kLiftEnd;")
            return
        # branch / jump: pairs[-2] is the branch, pairs[-1] its delay slot
        for pc in pairs[:-2]:
            self.emit_pair(pc, w)
        bpc, slot = pairs[-2], pairs[-1]
        p = self.code[bpc]
        w("    // %04X %s" % (bpc, self.lower_name(p)))
        w("    {")
        self.emit_pair(bpc, w, branch=True, indent="        ")
        if slot in self.code:
            self.emit_pair(slot, w, indent="        ")
        if bpc in self.dynamic:
            w("        switch (dyn)")
            w("        {")
            for t in self.dynamic[bpc]:
                w("        case 0x%04Xu:" % t)
                w(self.go(t, "            "))
            w("        default:")
            w("            return handoff(vu, bk, dyn);")
            w("        }")
        elif p.is_jump:
            w("        switch (jt)")
            w("        {")
            for b in sorted(self.blocks):
                w("        case 0x%04Xu: goto B_%04X;" % (b, b))
            w("        default:")
            w("            return handoff(vu, bk, jt);")
            w("        }")
        elif p.op in (0x20, 0x21):
            w(self.go(p.branch_target, "        "))
        else:
            w("        if (taken)")
            w(self.go(p.branch_target, "            "))
            w(self.go((bpc + 16) & PC_MASK, "        "))
        w("    }")

    def emit_upper(self, p):
        op = p.upper_op()
        uw, dest = p.uw, (p.uw >> 21) & 0xF
        if op is None or not dest:
            return ["vu.execUpper(0x%08Xu);" % uw] if op is None else []
        kind, operand, to_acc = op
        fd, fs, ft = (uw >> 6) & 0x1F, (uw >> 11) & 0x1F, (uw >> 16) & 0x1F
        lanes = [c for c in range(4) if dest & (8 >> c)]
        out = ["{ float r[4];"]
        a = lambda c: "nrm(VF(%d)[%d])" % (fs, c)
        acc = lambda c: "nrm(vu.m_state.acc[%d])" % c
        if operand.startswith("bc"):
            out.append("  const float b = nrm(VF(%d)[%s]);" % (ft, operand[2]))
            b = lambda c: "b"
        elif operand == "q":
            out.append("  const float b = nrm(vu.m_state.q);")
            b = lambda c: "b"
        elif operand == "i":
            out.append("  const float b = nrm(vu.m_state.i);")
            b = lambda c: "b"
        else:
            b = lambda c: "nrm(VF(%d)[%d])" % (ft, c)
        for c in lanes:
            if kind == "add":
                e = "%s + %s" % (a(c), b(c))
            elif kind == "sub":
                e = "%s - %s" % (a(c), b(c))
            elif kind == "mul":
                e = "%s * %s" % (a(c), b(c))
            elif kind == "madd":
                e = "%s + %s * %s" % (acc(c), a(c), b(c))
            elif kind == "msub":
                e = "%s - %s * %s" % (acc(c), a(c), b(c))
            elif kind in ("max", "mini"):
                e = "[](float x, float y) { return x %s y ? x : y; }(%s, %s)" % (">" if kind == "max" else "<", a(c), b(c))
            elif kind in ("opmsub", "opmul"):
                l, rr = (1, 2, 0, 3)[c], (2, 0, 1, 3)[c]
                prod = "nrm(VF(%d)[%d]) * nrm(VF(%d)[%d])" % (fs, l, ft, rr)
                e = "0.0f" if c == 3 else ("%s - %s" % (acc(c), prod) if kind == "opmsub" else prod)
            elif kind.startswith("itof"):
                sh = int(kind[4:])
                conv = "static_cast<float>(static_cast<int32_t>(lowBits(VF(%d)[%d])))" % (fs, c)
                e = conv if sh == 0 else "%s / %d.0f" % (conv, 1 << sh)
            elif kind.startswith("ftoi"):
                e = "ftoi(%s, %d.0f)" % (a(c), 1 << int(kind[4:]))
            elif kind == "abs":
                e = "std::fabs(%s)" % a(c)
            out.append("  r[%d] = %s;" % (c, e))
        if kind in ("max", "mini") or kind.startswith(("itof", "ftoi")) or kind == "abs":
            dst = ft if kind.startswith(("itof", "ftoi")) or kind == "abs" else fd
            if dst:
                for c in lanes:
                    out.append("  VF(%d)[%d] = r[%d];" % (dst, c, c))
        else:
            dst = "vu.m_state.acc" if to_acc else "VF(%d)" % fd
            if not to_acc and fd == 0:
                raise SystemExit("FMAC op writes VF0 at %04X" % p.pc)
            out.append("  fmacStore<0x%X, %s>(vu, 0x%08Xu, r, %s);"
                       % (dest, "true" if p.pc in self.mac_live else "false", uw, dst))
        out.append("}")
        return out

    @staticmethod
    def lower_name(p):
        if p.op in BRANCHES:
            return BRANCHES[p.op]
        if p.op in JUMPS:
            return JUMPS[p.op]
        return "op %02X" % p.op

    def emit_pair(self, pc, w, branch=False, indent="    "):
        p = self.code[pc]
        lines = []
        L = lines.append
        lo = p.lo
        ft, fs, fd = (lo >> 16) & 0x1F, (lo >> 11) & 0x1F, (lo >> 6) & 0x1F
        it, is_, id_ = ft & 0xF, fs & 0xF, fd & 0xF
        dest = (lo >> 21) & 0xF
        imm11 = sext(lo & 0x7FF, 11)

        L("// %04X  %08X %08X" % (pc, p.lo, p.up))
        L("step(vu);")

        # Explicit waits happen before the pair issues (the interpreter stalls the whole pair).
        if not p.ibit:
            if p.special in (0x38, 0x39, 0x3A, 0x3B):
                L("waitFdiv(vu);")
            elif p.special in EFU_SPECIALS:
                L("waitEfuResource(vu);")
            elif p.special == 0x7B:
                L("waitP(vu);")

        lower = []  # lower-op statements
        LO = lower.append

        def store_lanes(addr_expr, src_expr):
            for c in range(4):
                if dest & (8 >> c):
                    LO("std::memcpy(mem + %s + %du, &%s[%d], 4);" % (addr_expr, c * 4, src_expr, c))

        def load_lanes(reg, addr_expr):
            if reg == 0:
                return
            for c in range(4):
                if dest & (8 >> c):
                    LO("std::memcpy(&VF(%d)[%d], mem + %s + %du, 4);" % (reg, c, addr_expr, c * 4))

        def vi_write(reg, expr, delays_branch):
            if reg == 0:
                return
            if delays_branch:
                LO("bk.record(%d, VI(%d), vu.m_cycle);" % (reg, reg))
            LO("VI(%d) = static_cast<int16_t>(%s);" % (reg, expr))

        if branch:
            pass  # handled below
        elif p.lower_nop():
            pass
        elif p.op == 0x00:  # LQ
            if ft:
                LO("{ const uint32_t a = (static_cast<uint32_t>(VI(%d) + (%d)) * 16u) & dm;" % (is_, imm11))
                load_lanes(ft, "a")
                LO("}")
        elif p.op == 0x01:  # SQ fs, imm(it)
            LO("{ const uint32_t a = (static_cast<uint32_t>(VI(%d) + (%d)) * 16u) & dm;" % (it, imm11))
            store_lanes("a", "VF(%d)" % fs)
            LO("}")
        elif p.op == 0x04:  # ILW it, imm(is)
            comp = 0 if dest & 8 else 1 if dest & 4 else 2 if dest & 2 else 3
            if it:
                LO("{ const uint32_t a = (static_cast<uint32_t>(VI(%d) + (%d)) * 16u) & dm; uint32_t v; "
                   "std::memcpy(&v, mem + a + %du, 4);" % (is_, imm11, comp * 4))
                LO("  VI(%d) = static_cast<int16_t>(v & 0xFFFFu); }" % it)
        elif p.op == 0x05:  # ISW it, imm(is)
            LO("{ const uint32_t a = (static_cast<uint32_t>(VI(%d) + (%d)) * 16u) & dm; "
               "const uint32_t v = static_cast<uint16_t>(VI(%d));" % (is_, imm11, it))
            for c in range(4):
                if dest & (8 >> c):
                    LO("  std::memcpy(mem + a + %du, &v, 4);" % (c * 4))
            LO("}")
        elif p.op in (0x08, 0x09):  # IADDIU / ISUBIU
            imm15 = (lo & 0x7FF) | ((lo >> 10) & 0x7800)
            vi_write(it, "VI(%d) %s %d" % (is_, "+" if p.op == 0x08 else "-", imm15), True)
        elif p.op == 0x10:  # FCEQ
            LO("VI(1) = (vu.m_state.clip & 0xFFFFFFu) == 0x%06Xu ? 1 : 0;" % (lo & 0xFFFFFF))
        elif p.op == 0x11:  # FCSET
            LO("vu.queueFcset(0x%06Xu);" % (lo & 0xFFFFFF))
        elif p.op == 0x12:  # FCAND
            LO("VI(1) = (vu.m_state.clip & 0x%06Xu) != 0u ? 1 : 0;" % (lo & 0xFFFFFF))
        elif p.op == 0x13:  # FCOR
            LO("VI(1) = ((vu.m_state.clip | 0x%06Xu) & 0xFFFFFFu) == 0xFFFFFFu ? 1 : 0;" % (lo & 0xFFFFFF))
        elif p.op == 0x1A:  # FMAND
            vi_write(it, "vu.m_state.mac & static_cast<uint16_t>(VI(%d))" % is_, False)
        elif p.op == 0x1C:  # FCGET
            vi_write(it, "vu.m_state.clip & 0x0FFFu", False)
        elif p.op == 0x40 and (lo & 0x3F) in (0x30, 0x31, 0x34, 0x35):  # IADD ISUB IAND IOR
            sym = {0x30: "+", 0x31: "-", 0x34: "&", 0x35: "|"}[lo & 0x3F]
            vi_write(id_, "VI(%d) %s VI(%d)" % (is_, sym, it), True)
        elif p.op == 0x40 and (lo & 0x3F) == 0x32:  # IADDI
            vi_write(it, "VI(%d) + (%d)" % (is_, sext((lo >> 6) & 0x1F, 5)), True)
        elif p.special == 0x30:  # MOVE
            if ft and dest:
                if ft == fs:
                    pass
                else:
                    for c in range(4):
                        if dest & (8 >> c):
                            LO("VF(%d)[%d] = VF(%d)[%d];" % (ft, c, fs, c))
        elif p.special == 0x31:  # MR32
            if ft and dest:
                LO("{ const float t[4] = {VF(%d)[1], VF(%d)[2], VF(%d)[3], VF(%d)[0]};" % (fs, fs, fs, fs))
                for c in range(4):
                    if dest & (8 >> c):
                        LO("  VF(%d)[%d] = t[%d];" % (ft, c, c))
                LO("}")
        elif p.special in (0x34, 0x36):  # LQI / LQD
            if p.special == 0x36:
                vi_write(is_, "VI(%d) - 1" % is_, True)
            LO("{ const uint32_t a = (static_cast<uint32_t>(static_cast<uint16_t>(VI(%d))) * 16u) & dm;" % is_)
            load_lanes(ft, "a")
            LO("}")
            if p.special == 0x34:
                vi_write(is_, "VI(%d) + 1" % is_, True)
        elif p.special in (0x35, 0x37):  # SQI / SQD
            if p.special == 0x37:
                vi_write(it, "VI(%d) - 1" % it, True)
            LO("{ const uint32_t a = (static_cast<uint32_t>(static_cast<uint16_t>(VI(%d))) * 16u) & dm;" % it)
            store_lanes("a", "VF(%d)" % fs)
            LO("}")
            if p.special == 0x35:
                vi_write(it, "VI(%d) + 1" % it, True)
        elif p.special == 0x3B or p.special == 0x7B:  # WAITQ / WAITP: the wait above is the whole op
            pass
        elif p.special == 0x3C:  # MTIR
            comp = (lo >> 21) & 3
            vi_write(it, "lowBits(VF(%d)[%d]) & 0xFFFFu" % (fs, comp), True)
        elif p.special == 0x3D:  # MFIR
            if ft and dest:
                LO("{ const int32_t v = static_cast<int16_t>(VI(%d)); float f; std::memcpy(&f, &v, 4);" % is_)
                for c in range(4):
                    if dest & (8 >> c):
                        LO("  VF(%d)[%d] = f;" % (ft, c))
                LO("}")
        elif p.special == 0x64:  # MFP
            if ft and dest:
                for c in range(4):
                    if dest & (8 >> c):
                        LO("VF(%d)[%d] = vu.m_state.p;" % (ft, c))
        elif p.special in (0x68, 0x69):  # XTOP / XITOP
            vi_write(it, "vu.m_state.%s & 0x3FFu" % ("top" if p.special == 0x68 else "itop"), False)
        elif p.special == 0x6C:  # XGKICK
            LO("xgkick(vu, static_cast<uint16_t>(VI(%d)));" % is_)
        elif p.special in (0x38, 0x39, 0x3A) or p.special in EFU_SPECIALS:
            LO("vu.execLower(0x%08Xu, mem, run.dataSize, *run.gs, run.memory, 0x%08Xu);" % (lo, p.up))
        else:
            raise SystemExit("lifter: unhandled lower op %08X at %04X" % (lo, pc))

        # Branch condition / jump target: read before this pair's own effects (VI with backup).
        if branch:
            if pc in self.dynamic:
                L("const uint32_t dyn = dynamicBranchTarget(run, 0x%04Xu);" % pc)
            elif p.is_jump:
                L("const uint32_t jt = (static_cast<uint32_t>(static_cast<uint16_t>(bv(vu, bk, %d))) * 8u) & 0x3FFFu;" % is_)
            elif p.op not in (0x20, 0x21):
                cond = {0x28: "bv(vu, bk, %d) == bv(vu, bk, %d)" % (is_, it),
                        0x29: "bv(vu, bk, %d) != bv(vu, bk, %d)" % (is_, it),
                        0x2C: "bv(vu, bk, %d) < 0" % is_, 0x2D: "bv(vu, bk, %d) > 0" % is_,
                        0x2E: "bv(vu, bk, %d) <= 0" % is_, 0x2F: "bv(vu, bk, %d) >= 0" % is_}[p.op]
                L("const bool taken = %s;" % cond)

        # Upper, then lower; the interpreter's same-register rule: when the lower op touches the VF
        # register the upper writes, the lower sees the old value and the upper's result wins.
        uw = p.upper_vf_write()
        shadow = uw and (uw in p.lower_vf_regs()) and lower
        if shadow:
            L("{ float o[4], u[4]; std::memcpy(o, VF(%d), 16);" % uw)
        if not p.upper_nop():
            lines.extend(self.emit_upper(p))
        if p.ibit:
            L("{ float f; const uint32_t b = 0x%08Xu; std::memcpy(&f, &b, 4); vu.m_state.i = vu.normalizeOperand(f); }" % p.lo)
        if shadow:
            L("  std::memcpy(u, VF(%d), 16); std::memcpy(VF(%d), o, 16);" % (uw, uw))
        lines.extend(lower)
        if shadow:
            L("  std::memcpy(VF(%d), u, 16); }" % uw)
        if branch and p.op in (0x21, 0x25) and it:
            L("VI(%d) = %d;" % (it, (pc + 16) // 8))
        L("tick(vu);")
        for line in lines:
            w(indent + line)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=os.path.join(ROOT, "iso/SLUS_202.94"))
    ap.add_argument("--config", default=os.path.join(ROOT, "config/vu1_lift.json"))
    ap.add_argument("--exact-flags", action="store_true",
                    help="compute MAC/status for every FMAC op (lets PS2X_VU1_VERIFY compare them too)")
    ap.add_argument("--break-vi-backup", action="store_true",
                    help="testing only: ignore the VI branch backup, to check that PS2X_VU1_VERIFY catches it")
    ap.add_argument("--break-move", action="store_true",
                    help="testing only: loads read x from y, which PS2X_VU1_VERIFY must report")
    ap.add_argument("-o", "--output", default=os.path.join(
        ROOT, "external/PS2Recomp/ps2xRuntime/src/lib/vu/generated/ps2_vu1_lifted_gitaroo.inc"))
    args = ap.parse_args(argv)
    cfg = json.load(open(args.config))
    image = load_image(args.iso, int(cfg["elf_va"], 16), cfg["pairs"])
    lifter = Lifter(image, cfg)
    text = lifter.emit(args.exact_flags)
    if args.break_vi_backup:
        text = text.replace("bk.record(", "(void)bk; (void)(")
    if args.break_move:
        text = text.replace("[0], mem + a + 0u, 4);", "[0], mem + a + 4u, 4);")
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", newline="\n") as f:
        f.write(text)
    reach = {pc for b in lifter.blocks.values() for pc in b[0]}
    print("wrote %s: %d blocks, %d/%d pairs reachable, %d lines; MAC/status kept for %d FMAC ops%s"
          % (args.output, len(lifter.blocks), len(reach), len(lifter.code), text.count("\n"),
             len(lifter.mac_live), " (exact)" if lifter.flags_exact else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
