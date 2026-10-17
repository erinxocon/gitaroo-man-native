#!/usr/bin/python3
"""Compare a recorded SPU2 WAV against a PSS movie's PCM: per-second RMS and best correlation."""
import sys
import wave

import numpy as np


def main():
    wav, raw = sys.argv[1], sys.argv[2]
    w = wave.open(wav)
    out = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, 2).astype(float)
    data = np.frombuffer(open(raw, "rb").read(), dtype=np.int16)
    blocks = data[:len(data) // 512 * 512].reshape(-1, 2, 256)
    src = np.stack([blocks[:, 0, :].ravel(), blocks[:, 1, :].ravel()], axis=1).astype(float)
    print("secs", round(len(out) / 48000, 1), "rms/s", [int(np.sqrt((out[s:s + 48000] ** 2).mean())) for s in range(0, len(out), 48000)])
    ref = src[48000:96000, 0]
    best = (0.0, 0)
    for off in range(0, max(1, len(out) - len(ref)), 120):
        seg = out[off:off + len(ref), 0]
        if len(seg) < len(ref):
            break
        a, b = seg - seg.mean(), ref - ref.mean()
        c = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
        if c > best[0]:
            best = (c, off)
    print("best correlation", round(best[0], 3), "at", round(best[1] / 48000, 2), "s")


if __name__ == "__main__":
    main()
