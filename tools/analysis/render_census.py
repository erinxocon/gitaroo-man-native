#!/usr/bin/env python3
"""Name the methods in the last PS2X_RENDER_CENSUS report of a log.

Usage: python3 tools/analysis/render_census.py logs/<run>.log
Prints the AIR.NGP render-context methods by calls per frame, with their vtable offset, the symbol
from config/symbols/*.txt and the top callers (also named when they fall inside a known function).
"""
import bisect
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_symbols():
    syms = {}
    for path in glob.glob(os.path.join(ROOT, "config/symbols/*.txt")):
        for line in open(path):
            parts = line.split()
            if len(parts) >= 2 and parts[1].startswith("0x"):
                syms[int(parts[1], 16)] = parts[0]
    return syms


def main():
    log = open(sys.argv[1], errors="replace").read().splitlines()
    starts = [i for i, l in enumerate(log) if "[render-census] after" in l]
    if not starts:
        sys.exit("no census report in the log")
    block = []
    for l in log[starts[-1] + 1:]:
        if "[render-census] +0x" not in l:
            break
        block.append(l)
    syms = load_symbols()
    addrs = sorted(syms)

    def owner(pc):
        i = bisect.bisect_right(addrs, pc) - 1
        return f"{syms[addrs[i]]}+0x{pc - addrs[i]:x}" if i >= 0 and pc - addrs[i] < 0x4000 else f"0x{pc:x}"

    rows = []
    for l in block:
        m = re.search(r"\+0x([0-9a-f]+) 0x([0-9a-f]+) calls=(\d+) per-frame=([\d.]+) callers=(\d+) top:(.*)", l)
        off, addr, calls, pf, ncallers, top = m.groups()
        callers = [(int(a, 16), int(n)) for a, n in re.findall(r"([0-9a-f]+)\((\d+)\)", top)]
        rows.append((float(pf), int(off, 16), int(addr, 16), int(calls), int(ncallers), callers))
    print(log[starts[-1]].split("] ", 1)[1])
    print(f"{'per frame':>10} {'offset':>6}  {'method':32} {'callers':>7}  top callers")
    for pf, off, addr, calls, ncallers, callers in sorted(rows, reverse=True):
        name = syms.get(addr, f"0x{addr:x}")
        tops = ", ".join(f"{owner(ra)}" for ra, n in callers[:3])
        print(f"{pf:10.1f} +0x{off:03x}  {name:32} {ncallers:7}  {tops}")


if __name__ == "__main__":
    main()
