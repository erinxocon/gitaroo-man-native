#!/usr/bin/env python3
"""Compare the MSTREAM requests and replies of two runs (PS2X_TRACE_MSTREAM=1 logs).

  python3 tools/audio/mstream_diff.py <reference.log> <native.log> [--limit N]

Lists, in order, every request whose command, arguments or reply differ. The song clock (0x5600)
and status (0x0900) replies depend on timing, so they are compared separately: status changes per
stream with the song clock at the time, and the clock's rate against the vsync tick.
"""
import argparse
import re

LINE = re.compile(r"\[mstream\] t=([\d.]+) tick=(\d+) ra=\w+ fno=(0x[0-9a-f]+) mode=\d+ ssize=(\d+) rsize=\d+ "
                  r"send=((?:[0-9a-f]{8} ?){8}) recv=((?:[0-9a-f]{8} ?){8})(?: tail=([0-9a-f]{8}))?")


def parse(path):
    out = []
    with open(path, errors="replace") as f:
        for line in f:
            m = LINE.search(line)
            if m:
                out.append(dict(t=float(m.group(1)), tick=int(m.group(2)), fno=int(m.group(3), 16), ssize=int(m.group(4)),
                                send=m.group(5).split(), recv=m.group(6).split(), tail=m.group(7)))
    return out


def key(r):
    send = r["send"][:r["ssize"] // 4] if r["ssize"] < 32 else r["send"]
    return (hex(r["fno"]), tuple(send), r["tail"] if r["ssize"] >= 64 else None)


def statuses(rows):
    last, clock, out = {}, 0, []
    for r in rows:
        if r["fno"] == 0x5600:
            clock = int(r["recv"][0], 16)
        if r["fno"] == 0x900:
            s, v = r["send"][0], r["recv"][0]
            if last.get(s) != v:
                out.append((s, v, clock, r["tick"]))
                last[s] = v
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reference")
    ap.add_argument("native")
    ap.add_argument("--limit", type=int, default=40)
    a = ap.parse_args()
    ref, nat = parse(a.reference), parse(a.native)
    timed = (0x5600, 0x900)
    rq = [r for r in ref if r["fno"] not in timed]
    nq = [r for r in nat if r["fno"] not in timed]
    n = min(len(rq), len(nq))
    print(f"requests (without clock/status polls): reference {len(rq)}, native {len(nq)}, compared {n}")
    diffs = 0
    for i in range(n):
        r, q = rq[i], nq[i]
        if key(r) != key(q):
            print(f"#{i}: request differs: ref {key(r)} vs native {key(q)}")
            diffs += 1
            break
        if r["recv"][:4] != q["recv"][:4]:
            diffs += 1
            if diffs <= a.limit:
                print(f"#{i} fno={hex(r['fno'])} send={' '.join(r['send'][:4])}: reply ref {' '.join(r['recv'][:4])} native {' '.join(q['recv'][:4])}")
    print(f"{diffs} differing replies")
    rs, ns = statuses(ref), statuses(nat)
    print("status changes (stream, status, song clock, tick): reference | native")
    for i in range(max(len(rs), len(ns))):
        x = rs[i] if i < len(rs) else ""
        y = ns[i] if i < len(ns) else ""
        print(f"  {x} | {y}")


if __name__ == "__main__":
    main()
