"""Compare our port's VU1 census (PS2X_VU1_CENSUS -> build/vu_census) with the ELF microprogram, the
PCSX2 reference logs, and what the Ace Combat 5 port's lifter (tools/vurecomp) would cover.

Usage: python3 tools/vu1lift/census_compare.py [census_dir]
Needs the AC5 clone at $AC5_DIR (default ~/repos/ext/ac5) for vurecomp's decoder/CFG."""
import sys, os, json, struct, collections, glob, re
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.environ.get("AC5_DIR", os.path.expanduser("~/repos/ext/ac5")), "tools"))
from vurecomp import vudec
from vurecomp.cfg import build_cfg
D = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build/vu_census")
man = json.load(open(D + "/manifest.json")); g = json.load(open(D + "/gitaroo_census.json"))

def elf_microprogram():
    """AIR.NGP VU1 program: ELF VA 0x244480..0x2470E0, uploaded to micro address 0."""
    d = open(os.path.join(ROOT, "iso/SLUS_202.94"), "rb").read()
    ph = struct.unpack_from("<I", d, 0x1C)[0]
    for i in range(struct.unpack_from("<H", d, 0x2C)[0]):
        t, off, va, _, fs, _ = struct.unpack_from("<6I", d, ph + i * 32)
        if t == 1 and va <= 0x244480 < va + fs:
            return d[off + 0x244480 - va:off + 0x2470E0 - va]
elf = elf_microprogram()
# microVU "Cached Prog" PCs = MSCAL entries + JR/JALR targets.
PCSX2_ENTRIES = set()
for log in glob.glob(os.path.join(ROOT, "logs/pcsx2_*.log")):
    PCSX2_ENTRIES |= {int(m, 16) for m in re.findall(r"microVU1: Cached Prog .*?PC=([0-9a-f]+)", open(log, errors="replace").read())}
def tgt(lo):
    i = lo & 0x7FF
    if i & 0x400: i -= 0x800
    return (0x1B78 + i * 8) & 0x3FF8

gi = {im["crc"]: im for im in g["images"]}
tot_pairs = sum(p["instructions"] for p in man["programs"]); tot_cyc = sum(im["cycles"] for im in g["images"])
print("== images: %d, %s pairs, %s cycles (stall overhead %.1f%%)" % (len(man["programs"]), f"{tot_pairs:,}", f"{tot_cyc:,}", 100.0 * (tot_cyc - tot_pairs) / max(tot_cyc, 1)))
entries_all = collections.Counter(); pairs_by_entry = collections.Counter(); cyc_by_entry = collections.Counter()
for p in man["programs"]:
    img = open(os.path.join(D, p["image"]), "rb").read()
    diffs = [pc for pc in range(0, 0x4000, 8) if img[pc:pc+8] != (elf[pc:pc+8] if pc < len(elf) else bytes(8))]
    w = gi[p["crc"]]["lower_1b70"]
    print("  %s  1b70 -> %04x  activations %8d  pairs %5.1f%%  differs from ELF at: %s" % (
        p["crc"][:12], tgt(w), p["activations"], 100.0 * p["instructions"] / max(tot_pairs, 1),
        ",".join("%04x" % d for d in diffs if d != 0x1B70)[:120] or "-"))
    for k, (n, pr, cy) in gi[p["crc"]]["entries"].items():
        entries_all[int(k)] += n; pairs_by_entry[int(k)] += pr; cyc_by_entry[int(k)] += cy

ours = set(entries_all)
targets_seen = {t for _, t, _ in g["jumps"]}
print("\n== entries: ours %d MSCAL + %d JR targets = %d; PCSX2 microVU block starts %d" % (
    len(ours), len(targets_seen - ours), len(ours | targets_seen), len(PCSX2_ENTRIES)))
print("  only ours :", " ".join("%04x" % e for e in sorted((ours | targets_seen) - PCSX2_ENTRIES)) or "-")
print("  only PCSX2:", " ".join("%04x" % e for e in sorted(PCSX2_ENTRIES - ours - targets_seen)) or "-")
print("\n== hottest entries (share of pairs, cycles/pair):")
for e, n in pairs_by_entry.most_common(12):
    print("  %04x  calls %8d  pairs %5.1f%%  avg %6.0f pairs/call  %.2f cyc/pair" % (
        e, entries_all[e], 100.0 * n / tot_pairs, n / max(entries_all[e], 1), cyc_by_entry[e] / max(n, 1)))

jumps = g["jumps"]
jt = collections.defaultdict(collections.Counter)
for pc, t, n in jumps: jt[pc][t] += n
print("\n== JR/JALR: %d sites executed, %d distinct (site,target) pairs" % (len(jt), len(jumps)))
for pc in sorted(jt):
    print("  %04x -> %s" % (pc, " ".join("%04x(x%d)" % (t, n) for t, n in jt[pc].most_common())))

# AC5 coverage: CFG from census entries only (AC5) vs entries + observed JR targets
targets = {t for _, t, _ in jumps}
print("\n== AC5-style lifting coverage (weighted by executed pairs)")
for label, roots in (("AC5 (census entries only)", ours), ("+ observed JR targets as roots", ours | targets)):
    cov = done = 0
    for p in man["programs"]:
        img = open(os.path.join(D, p["image"]), "rb").read()
        code = vudec.decode_image(img, p["code_lo"], p["code_hi"])
        cfg = build_cfg(code, sorted(roots))
        reach = {pc for b in cfg.blocks.values() for pc in b.pairs}
        jrdyn = [t for t in targets if t not in cfg.blocks]
        for pc, n in gi[p["crc"]]["pc_count"].items():
            done += n; cov += n if int(pc) in reach else 0
    print("  %-34s %5.1f%% of executed pairs are in lifted code; JR targets not a block start: %d" % (
        label, 100.0 * cov / max(done, 1), len(jrdyn)))

print("\n== timing hazards (instruction model vs our cycle model)")
for h in g["hazards"]:
    if not (h["disagree"] or h["instr_pending"]): continue
    print("  %04x %-5s reads %9d  instr-pending %9d  cycle-pending %9d  DISAGREE %d" % (
        h["pc"], h["kind"], h["reads"], h["instr_pending"], h["cycle_pending"], h["disagree"]))
print("  sites %d, reads %d, disagreements %d" % (len(g["hazards"]), sum(h["reads"] for h in g["hazards"]),
      sum(h["disagree"] for h in g["hazards"])))
print("  VI branch-backup reads (value differs):", {"%04x" % int(k): v for k, v in g["vi_branch_backup"].items()} or "none")
print("  stores into a not-yet-copied XGKICK packet:", g["xgkick_store_races"] or "none")
