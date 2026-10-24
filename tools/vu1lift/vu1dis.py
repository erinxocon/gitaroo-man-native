#!/usr/bin/env python3
"""Disassemble Gitaroo Man's VU1 microprogram (read from the ELF, like vu1lift.py).

Usage: python3 tools/vu1lift/vu1dis.py [--iso iso/SLUS_202.94] [--from 0x1B68] [--to 0x2800]
                                       [--b1b70 0x1B80]

Output: one line per pair, "pc  upper | lower", with branch targets as absolute micro addresses.
The pair at 0x1B70 is rewritten by the EE at run time (see docs/vu1-lifter.md); --b1b70 shows it
as a B to the given target.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vu1lift import ROOT, PC_MASK, load_image, sext  # noqa: E402

LANES = "xyzw"


def mask(dest):
    return "".join(c for i, c in enumerate(LANES) if (dest >> (3 - i)) & 1)


def upper(w):
    w &= 0x07FFFFFF
    dest, ft, fs, fd, bc = (w >> 21) & 0xF, (w >> 16) & 0x1F, (w >> 11) & 0x1F, (w >> 6) & 0x1F, w & 3
    f = w & 0x3F
    m = mask(dest)
    if (f & 0x3C) != 0x3C:
        if f < 0x1C:
            n = ("ADD", "SUB", "MADD", "MSUB", "MAX", "MINI", "MUL")[f >> 2]
            return "%s%s.%s vf%d, vf%d, vf%d%s" % (n, LANES[bc], m, fd, fs, ft, LANES[bc])
        main = {0x1C: "MULq", 0x1D: "MAXi", 0x1E: "MULi", 0x1F: "MINIi", 0x20: "ADDq", 0x21: "MADDq",
                0x22: "ADDi", 0x23: "MADDi", 0x24: "SUBq", 0x25: "MSUBq", 0x26: "SUBi", 0x27: "MSUBi"}
        if f in main:
            return "%s.%s vf%d, vf%d, %s" % (main[f], m, fd, fs, main[f][-1].upper())
        vec = {0x28: "ADD", 0x29: "MADD", 0x2A: "MUL", 0x2B: "MAX", 0x2C: "SUB", 0x2D: "MSUB",
               0x2E: "OPMSUB", 0x2F: "MINI"}
        if f in vec:
            return "%s.%s vf%d, vf%d, vf%d" % (vec[f], m, fd, fs, ft)
        return "?up %08x" % w
    sub = (w & 3) | ((w >> 4) & 0x7C)
    if sub < 0x10:
        n = ("ADDA", "SUBA", "MADDA", "MSUBA")[sub >> 2]
        return "%s%s.%s ACC, vf%d, vf%d%s" % (n, LANES[sub & 3], m, fs, ft, LANES[sub & 3])
    if sub < 0x18:
        n = ("ITOF", "FTOI")[(sub - 0x10) >> 2] + str((0, 4, 12, 15)[sub & 3])
        return "%s.%s vf%d, vf%d" % (n, m, ft, fs)
    if sub < 0x1C:
        return "MULA%s.%s ACC, vf%d, vf%d%s" % (LANES[sub & 3], m, fs, ft, LANES[sub & 3])
    if sub == 0x1D:
        return "ABS.%s vf%d, vf%d" % (m, ft, fs)
    if sub == 0x1F:
        return "CLIPw.xyz vf%d, vf%dw" % (fs, ft)
    if sub == 0x2F or sub == 0x30:
        return "NOP"
    acc = {0x1C: "MULAq", 0x1E: "MULAi", 0x20: "ADDAq", 0x21: "MADDAq", 0x22: "ADDAi",
           0x23: "MADDAi", 0x24: "SUBAq", 0x25: "MSUBAq", 0x26: "SUBAi", 0x27: "MSUBAi"}
    if sub in acc:
        return "%s.%s ACC, vf%d, %s" % (acc[sub], m, fs, acc[sub][-1].upper())
    vacc = {0x28: "ADDA", 0x29: "MADDA", 0x2A: "MULA", 0x2C: "SUBA", 0x2D: "MSUBA", 0x2E: "OPMULA"}
    if sub in vacc:
        return "%s.%s ACC, vf%d, vf%d" % (vacc[sub], m, fs, ft)
    return "?up %08x" % w


def lower(lo, pc):
    op = (lo >> 25) & 0x7F
    dest, it, is_, fd = (lo >> 21) & 0xF, (lo >> 16) & 0x1F, (lo >> 11) & 0x1F, (lo >> 6) & 0x1F
    ft, fs = it, is_
    m = mask(dest)
    imm11 = sext(lo & 0x7FF, 11)
    target = (pc + 8 + imm11 * 8) & PC_MASK
    if lo in (0, 0x8000033C):
        return "NOP"
    if op == 0x00:
        return "LQ.%s vf%d, %d(vi%d)" % (m, ft, imm11, is_)
    if op == 0x01:
        return "SQ.%s vf%d, %d(vi%d)" % (m, fs, imm11, it)
    if op == 0x04:
        return "ILW.%s vi%d, %d(vi%d)" % (m, it, imm11, is_)
    if op == 0x05:
        return "ISW.%s vi%d, %d(vi%d)" % (m, it, imm11, is_)
    if op in (0x08, 0x09):
        imm15 = ((lo >> 10) & 0x7800) | (lo & 0x7FF)
        return "%s vi%d, vi%d, %d" % (("IADDIU", "ISUBIU")[op & 1], it, is_, imm15)
    if 0x10 <= op <= 0x13:
        return "%s 0x%06x" % (("FCEQ", "FCSET", "FCAND", "FCOR")[op & 3], lo & 0xFFFFFF)
    if 0x14 <= op <= 0x17:
        imm12 = ((lo >> 10) & 0x800) | (lo & 0x7FF)
        n = ("FSEQ", "FSSET", "FSAND", "FSOR")[op & 3]
        return "%s vi%d, 0x%03x" % (n, it, imm12) if n != "FSSET" else "FSSET 0x%03x" % imm12
    if op in (0x18, 0x1A, 0x1B):
        return "%s vi%d, vi%d" % ({0x18: "FMEQ", 0x1A: "FMAND", 0x1B: "FMOR"}[op], it, is_)
    if op == 0x1C:
        return "FCGET vi%d" % it
    if op == 0x20:
        return "B 0x%04x" % target
    if op == 0x21:
        return "BAL vi%d, 0x%04x" % (it, target)
    if op == 0x24:
        return "JR vi%d" % is_
    if op == 0x25:
        return "JALR vi%d, vi%d" % (it, is_)
    if op in (0x28, 0x29):
        return "%s vi%d, vi%d, 0x%04x" % (("IBEQ", "IBNE")[op & 1], it, is_, target)
    if 0x2C <= op <= 0x2F:
        return "%s vi%d, 0x%04x" % (("IBLTZ", "IBGTZ", "IBLEZ", "IBGEZ")[op & 3], is_, target)
    if op == 0x40:
        f = lo & 0x3F
        if f < 0x3C:
            if f in (0x30, 0x31, 0x34, 0x35):
                n = {0x30: "IADD", 0x31: "ISUB", 0x34: "IAND", 0x35: "IOR"}[f]
                return "%s vi%d, vi%d, vi%d" % (n, fd, is_, it)
            if f == 0x32:
                return "IADDI vi%d, vi%d, %d" % (it, is_, sext(fd, 5))
            return "?lo %08x" % lo
        s = (lo & 3) | ((lo >> 4) & 0x7C)
        fsf, ftf = LANES[(lo >> 21) & 3], LANES[(lo >> 23) & 3]
        simple = {
            0x30: "MOVE.%s vf%d, vf%d" % (m, ft, fs), 0x31: "MR32.%s vf%d, vf%d" % (m, ft, fs),
            0x34: "LQI.%s vf%d, (vi%d++)" % (m, ft, is_), 0x35: "SQI.%s vf%d, (vi%d++)" % (m, fs, it),
            0x36: "LQD.%s vf%d, (--vi%d)" % (m, ft, is_), 0x37: "SQD.%s vf%d, (--vi%d)" % (m, fs, it),
            0x38: "DIV Q, vf%d%s, vf%d%s" % (fs, fsf, ft, ftf), 0x39: "SQRT Q, vf%d%s" % (ft, ftf),
            0x3A: "RSQRT Q, vf%d%s, vf%d%s" % (fs, fsf, ft, ftf), 0x3B: "WAITQ",
            0x3C: "MTIR vi%d, vf%d%s" % (it, fs, fsf), 0x3D: "MFIR.%s vf%d, vi%d" % (m, ft, is_),
            0x3E: "ILWR.%s vi%d, (vi%d)" % (m, it, is_), 0x3F: "ISWR.%s vi%d, (vi%d)" % (m, it, is_),
            0x40: "RNEXT.%s vf%d, R" % (m, ft), 0x41: "RGET.%s vf%d, R" % (m, ft),
            0x42: "RINIT R, vf%d%s" % (fs, fsf), 0x43: "RXOR R, vf%d%s" % (fs, fsf),
            0x64: "MFP.%s vf%d, P" % (m, ft), 0x68: "XTOP vi%d" % it, 0x69: "XITOP vi%d" % it,
            0x6C: "XGKICK vi%d" % is_, 0x7B: "WAITP",
        }
        if s in simple:
            return simple[s]
        efu = {0x70: "ESADD", 0x71: "ERSADD", 0x72: "ELENG", 0x73: "ERLENG", 0x74: "EATANxy",
               0x75: "EATANxz", 0x76: "ESUM", 0x78: "ESQRT", 0x79: "ERSQRT", 0x7A: "ERCPR",
               0x7C: "ESIN", 0x7D: "EATAN", 0x7E: "EEXP"}
        if s in efu:
            vec = s in (0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x76)
            return "%s P, vf%d%s" % (efu[s], fs, "" if vec else fsf)
    return "?lo %08x" % lo


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=os.path.join(ROOT, "iso/SLUS_202.94"))
    ap.add_argument("--va", type=lambda s: int(s, 0), default=0x244480)
    ap.add_argument("--pairs", type=int, default=1420)
    ap.add_argument("--from", dest="lo", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--to", dest="hi", type=lambda s: int(s, 0), default=None)
    ap.add_argument("--b1b70", type=lambda s: int(s, 0), default=None)
    a = ap.parse_args(argv)
    image = load_image(a.iso, a.va, a.pairs)
    hi = a.hi if a.hi is not None else len(image)
    for pc in range(a.lo, min(hi, len(image)), 8):
        lo, up = struct.unpack_from("<II", image, pc)
        if pc == 0x1B70 and a.b1b70 is not None:
            lo = 0x40000000 | (((a.b1b70 - pc - 8) // 8) & 0x7FF)
        flags = "".join(c for c, b in zip("IEMDT", (31, 30, 29, 28, 27)) if up >> b & 1)
        low = "LOI 0x%08x (%g)" % (lo, struct.unpack("<f", struct.pack("<I", lo))[0]) \
            if up & 0x80000000 else lower(lo, pc)
        up_s = upper(up)
        if up_s == "NOP" and low == "NOP" and not flags:
            print("%04x  NOP" % pc)
            continue
        print("%04x %-5s %-36s | %s" % (pc, flags, up_s, low))


if __name__ == "__main__":
    main()
