#!/usr/bin/python3
"""Reference software replay of a traced GS frame (PS2X_TRACE_DRAWS_TICK output).

Supports what the Gitaroo title screen uses: untextured sprites, gouraud/textured triangles with
STQ coordinates, PSMT8 + CT32 CLUT textures decoded from an IMX file, MODULATE, ALPHA=(Cs-Cd)*As+Cd.
Usage: gsref.py <log> <imx> <out.png> [ofx ofy]
"""
import re
import struct
import sys

import numpy as np
from PIL import Image

W, H = 640, 224


def load_imx_texture(path):
    data = open(path, "rb").read()
    palette = np.frombuffer(data[0x28:0x28 + 1024], dtype=np.uint8).reshape(256, 4).astype(np.float64)
    indices = np.frombuffer(data[0x430:0x430 + 512 * 512], dtype=np.uint8).reshape(512, 512)
    return palette[indices]  # RGBA, alpha in PS2 0..128 scale


def parse_draws(log_path):
    vertex_re = re.compile(r"v\d=\(([-\d.e+]+),([-\d.e+]+) z=[-\d.e+]+ rgba=(\d+),(\d+),(\d+),(\d+) st=([-\d.e+]+),([-\d.e+]+) q=([-\d.e+]+)")
    draws = []
    for line in open(log_path, errors="replace"):
        if not line.startswith("[draw]"):
            continue
        fields = dict(re.findall(r" (prim|tme|abe|alpha)=(\S+)", line))
        verts = [tuple(float(x) for x in m) for m in vertex_re.findall(line)]
        draws.append((int(fields["prim"]), int(fields["tme"]), int(fields["abe"]), verts))
    return draws


def edge(ax, ay, bx, by, px, py):
    return (bx - ax) * (py - ay) - (by - ay) * (px - ax)


def main():
    log, imx, out = sys.argv[1:4]
    ofx = float(sys.argv[4]) if len(sys.argv) > 4 else 1728.0
    ofy = float(sys.argv[5]) if len(sys.argv) > 5 else 1936.0
    tex = load_imx_texture(imx)
    fb = np.zeros((H, W, 3))
    ys, xs = np.mgrid[0:H, 0:W]
    px, py = xs.astype(np.float64), ys.astype(np.float64)
    for prim, tme, abe, verts in parse_draws(log):
        if prim == 6 and len(verts) >= 2:
            (x0, y0, r, g, b, a, *_), (x1, y1, *_rest) = verts[0], verts[1]
            fb[int(y0 - ofy):int(y1 - ofy), int(x0 - ofx):int(x1 - ofx)] = (r, g, b)
            continue
        if prim not in (3, 4, 5) or len(verts) != 3:
            continue
        v = [(x - ofx, y - ofy, r, g, b, a, s, t, q) for (x, y, r, g, b, a, s, t, q) in verts]
        area = edge(v[0][0], v[0][1], v[1][0], v[1][1], v[2][0], v[2][1])
        if area == 0:
            continue
        w0 = edge(v[1][0], v[1][1], v[2][0], v[2][1], px, py) / area
        w1 = edge(v[2][0], v[2][1], v[0][0], v[0][1], px, py) / area
        w2 = 1.0 - w0 - w1
        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if not inside.any():
            continue
        interp = lambda i: w0 * v[0][i] + w1 * v[1][i] + w2 * v[2][i]
        color = np.stack([interp(2), interp(3), interp(4)], axis=-1)
        alpha = interp(5)
        if tme:
            q = interp(8)
            u = np.clip((interp(6) / q * 512).astype(int), 0, 511)
            t = np.clip((interp(7) / q * 512).astype(int), 0, 511)
            texel = tex[t, u]
            color = texel[..., :3] * color / 128.0
            alpha = texel[..., 3] * alpha / 128.0
        if abe:
            a = (alpha / 128.0)[..., None]
            src = color * a + fb * (1 - a)
        else:
            src = color
        fb[inside] = np.clip(src[inside], 0, 255)
    Image.fromarray(fb.astype(np.uint8)).save(out)


if __name__ == "__main__":
    main()
