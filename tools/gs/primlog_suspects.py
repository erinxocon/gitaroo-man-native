#!/usr/bin/env python3
"""Which GS draws lie under the pixels where the GPU renderer and the CPU reference disagree?

Usage: python3 tools/gs/primlog_suspects.py <dir>/primlog.txt <dir>/gpu_verify_TICK.ppm [--top N]

Inputs come from a run with PS2X_GS_GPU_VERIFY=1 PS2X_GS_GPU_PRIMLOG=N PS2X_DUMP_FRAMES=<dir>: the log
of every primitive drawn before verified frame N+1 and that frame's comparison (GPU | CPU | 8x
difference). Primitives are grouped by their GS state; each group is scored by how many differing
pixels its primitives' bounding boxes cover, and listed with the share of those pixels it covers.
"""
import argparse
from collections import defaultdict

from PIL import Image

FIELDS = None
STATE = ["type", "tme", "fst", "tbp", "tbw", "tpsm", "tw", "th", "tcc", "tfx", "cbp", "cpsm", "abe", "alpha",
         "fge", "test", "fbp", "fbw", "fpsm", "fbmsk", "zbp", "zpsm", "zmsk", "clamp", "tex1", "linear",
         "colclamp", "dthe", "fba", "texa", "iip"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("primlog")
    ap.add_argument("ppm")
    ap.add_argument("--top", type=int, default=12)
    a = ap.parse_args(argv)

    im = Image.open(a.ppm).convert("RGB")
    w3, h = im.size[0] // 3, im.size[1]
    diff = im.crop((2 * w3, 0, 3 * w3, h)).load()
    bad = [[1 if max(diff[x, y]) > 128 else 0 for x in range(w3)] for y in range(h)]
    # Summed-area table for counting differing pixels in a box.
    sat = [[0] * (w3 + 1) for _ in range(h + 1)]
    for y in range(h):
        row = 0
        for x in range(w3):
            row += bad[y][x]
            sat[y + 1][x + 1] = sat[y][x + 1] + row
    total = sat[h][w3]
    print("%d differing pixels (%.3f%%)" % (total, 100.0 * total / (w3 * h)))

    def count(x0, y0, x1, y1):
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(w3, int(x1) + 1), min(h, int(y1) + 1)
        if x1 <= x0 or y1 <= y0:
            return 0
        return sat[y1][x1] - sat[y0][x1] - sat[y1][x0] + sat[y0][x0]

    groups = defaultdict(lambda: [0, 0, 0, None])  # covered pixels, prims, prims touching, last index
    with open(a.primlog) as f:
        header = f.readline().lstrip("# ").split()
        for line in f:
            cols = line.split()
            if len(cols) != len(header):
                continue  # the last line can be cut short when the run ends
            p = dict(zip(header, cols))
            n = int(p["nv"])
            xs = [float(p["x%d" % i]) for i in range(max(n, 1))]
            ys = [float(p["y%d" % i]) for i in range(max(n, 1))]
            key = tuple((k, p[k]) for k in STATE)
            g = groups[key]
            c = count(min(xs), min(ys), max(xs), max(ys))
            g[0] += c
            g[1] += 1
            g[2] += 1 if c else 0
            g[3] = int(p["idx"])
    ranked = sorted(groups.items(), key=lambda kv: -kv[1][0])
    for key, (covered, prims, touching, last) in ranked[:a.top]:
        if not covered:
            break
        state = " ".join("%s=%s" % kv for kv in key)
        print("covers %6d over %5d/%5d prims, last #%d: %s" % (covered, touching, prims, last, state))


if __name__ == "__main__":
    main()
