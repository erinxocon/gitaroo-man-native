"""Summarize a PCSX2 capture session made with tools/pcsx2/pine.py collect.

Usage: python3 tools/pcsx2/analyze_session.py <pcsx2 log> <session dir>
Maps every archived save state (NNN/) to the log (stage, section, FMV), checks VU1 micro memory
against the ELF microprogram, histograms the in-stream 0x1B70 patches still in EE RAM, and writes
<session dir>/timeline.txt. Prints the per-save table."""
import re, struct, sys, os, collections
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG, S2 = sys.argv[1], sys.argv[2]


def elf_microprogram():
    d = open(os.path.join(ROOT, "iso/SLUS_202.94"), "rb").read()
    ph = struct.unpack_from("<I", d, 0x1C)[0]
    for i in range(struct.unpack_from("<H", d, 0x2C)[0]):
        t, off, va, _, fs, _ = struct.unpack_from("<6I", d, ph + i * 32)
        if t == 1 and va <= 0x244480 < va + fs:
            return d[off + 0x244480 - va:off + 0x2470E0 - va]


elfvu = elf_microprogram()

STAGE_KEYS = [("PUNPAS.XG", "1"), ("ST02BG1", "2"), ("ST03BG1", "3"), ("STREAM01", "4"), ("ST05BG1", "5"),
              ("ST06BG01", "6"), ("ST07BG", "7"), ("ST08BG1", "8"), ("ST09BG1", "9"), ("ST10BG1", "10")]
ts = re.compile(r"^\[\s*([0-9.]+)\] (.*)")
stage, section, fmv, menu = "boot", None, False, ""
saves, events = [], []
vu1_new = []
seen_pc = set()
for line in open(LOG, errors="replace"):
    m = ts.match(line)
    if not m:
        continue
    t, msg = float(m.group(1)), m.group(2)
    for k, name in STAGE_KEYS:
        if "Loading model file " + k in msg and not stage == "stage " + name:
            stage = "stage " + name; section = None
            events.append((t, "STAGE " + name))
    if "Loading model file U1_ROOM.XG" in msg:
        stage = "single-play intro"; events.append((t, "single-play intro scene"))
    if "Welcome to GitarooMan" in msg:
        stage, section = "boot/menu", None; events.append((t, "game boot banner"))
    if msg.startswith("Switched to section:"):
        s = int(msg.split(":")[1])
        if s != section:
            section = s
    elif msg == "FMV started":
        fmv = True; events.append((t, "FMV start"))
    elif msg == "FMV ended":
        fmv = False; events.append((t, "FMV end"))
    elif "Saving state to slot" in msg:
        saves.append((t, stage, section, fmv))
    elif "Cached Prog = [000]" in msg and "VU1" in msg:
        events.append((t, "microVU1 cache cleared (reset/state load)"))
    mm = re.search(r"VU1: Cached Prog .*PC=([0-9a-f]+)\] \[List=(\d+)", msg)
    if mm and (mm.group(1), mm.group(2)) not in seen_pc:
        seen_pc.add((mm.group(1), mm.group(2)))
        vu1_new.append((t, stage, mm.group(1), int(mm.group(2))))

def b_target(lo, pc=0x1B70):
    imm = lo & 0x7FF
    if imm & 0x400: imm -= 0x800
    return (pc + 8 + imm * 8) & 0x3FF8

PATCH = struct.pack("<I", 0x4A01036E)
rows = []
allhist = collections.Counter()
dirs = sorted(d for d in os.listdir(S2) if re.fullmatch(r"\d{3}", d))
for i, d in enumerate(dirs):
    p = os.path.join(S2, d)
    micro = open(p + "/vu1MicroMem.bin", "rb").read()
    diffs = [pc for pc in range(0, len(elfvu), 8) if micro[pc:pc+8] != elfvu[pc:pc+8]]
    lo, up = struct.unpack_from("<II", micro, 0x1B70)
    ee = open(p + "/eeMemory.bin", "rb").read()
    hist = collections.Counter()
    pos = ee.find(PATCH)
    while pos >= 0:
        if pos % 4 == 0 and pos + 12 <= len(ee):
            l2, = struct.unpack_from("<I", ee, pos + 4)
            if (l2 >> 25) == 0x20:
                hist[b_target(l2)] += 1
        pos = ee.find(PATCH, pos + 1)
    allhist.update(hist)
    t, st, sec, f = saves[i] if i < len(saves) else (None, "?", None, False)
    rows.append((d, t, st, sec, f, b_target(lo), ["%04x" % x for x in diffs if x != 0x1B70], hist))

with open(os.path.join(S2, "timeline.txt"), "w") as out:
    evs = events + [(t, "VU1 new entry %s (variant %02d) [%s]" % (pc, v, st)) for t, st, pc, v in vu1_new] \
        + [(r[1], "SAVE #%s" % r[0]) for r in rows if r[1] is not None]
    for t, e in sorted(evs):
        out.write("%9.2f  %s\n" % (t, e))

print("saves in log:", len(saves), " state dirs:", len(dirs))
print("%-4s %8s %-22s %4s %3s %6s  %-10s %s" % ("#", "t", "stage", "sec", "fmv", "1b70->", "microdiff", "MPG@36E patches in EE RAM (target:count)"))
for d, t, st, sec, f, tgt, diffs, hist in rows:
    print("%-4s %8.1f %-22s %4s %3s %6s  %-10s %s" % (d, t or -1, st, "-" if sec is None else sec, "Y" if f else "", "%04x" % tgt,
          ",".join(diffs) or "-", " ".join("%04x:%d" % kv for kv in sorted(hist.items()))))
print("\nall-state patch target totals:", " ".join("%04x:%d" % kv for kv in sorted(allhist.items())))
print("\nVU1 entries first seen per stage:")
for t, st, pc, v in vu1_new:
    print("  %9.2f %-22s %s v%d" % (t, st, pc, v))
