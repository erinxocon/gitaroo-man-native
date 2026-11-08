#!/usr/bin/env python3
"""Compare two 48 kHz stereo 16-bit WAV dumps (PS2X_AUDIO_DUMP) of the same scripted run.

  python3 tools/audio/wav_compare.py <reference.wav> <native.wav> [--window 10]

For each window: the lag that best aligns native to reference (cross-correlation over +-50 ms),
the levels (RMS, dBFS) and the residual after alignment (dB below the reference). Runs drift apart
where the game's timing differed, so each window is aligned on its own.
"""
import argparse
import math
import sys
import wave

import numpy as np


def load(path):
    with wave.open(path, "rb") as w:
        if w.getnchannels() != 2 or w.getsampwidth() != 2:
            sys.exit(f"{path}: want 16-bit stereo")
        frames = w.readframes(w.getnframes())
    return np.frombuffer(frames, dtype="<i2").reshape(-1, 2).astype(np.float64), 48000


def db(x):
    return 20 * math.log10(x) if x > 0 else -999.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reference")
    ap.add_argument("native")
    ap.add_argument("--window", type=float, default=10.0, help="seconds")
    ap.add_argument("--max-lag", type=float, default=0.05, help="seconds")
    a = ap.parse_args()
    ref, rate = load(a.reference)
    nat, _ = load(a.native)
    win = int(a.window * rate)
    lag_max = int(a.max_lag * rate)
    print(f"reference {len(ref) / rate:.1f} s, native {len(nat) / rate:.1f} s")
    print("   time   ref dBFS  nat dBFS    lag ms  residual dB")
    for start in range(lag_max, min(len(ref), len(nat)) - win - lag_max, win):
        r = ref[start:start + win].mean(axis=1)
        rms_r = math.sqrt((r ** 2).mean())
        seg = nat[start - lag_max:start + win + lag_max].mean(axis=1)
        rms_n = math.sqrt((nat[start:start + win].mean(axis=1) ** 2).mean())
        if rms_r < 1.0 and rms_n < 1.0:
            print(f"{start / rate:7.1f}   silent    silent")
            continue
        # FFT cross-correlation of the reference window against the padded native segment.
        n = 1 << (len(seg) + len(r)).bit_length()
        corr = np.fft.irfft(np.fft.rfft(seg, n) * np.conj(np.fft.rfft(r, n)), n)[:2 * lag_max + 1]
        lag = int(np.argmax(corr)) - lag_max
        aligned = nat[start + lag:start + lag + win].mean(axis=1)
        res = math.sqrt(((aligned - r) ** 2).mean())
        print(f"{start / rate:7.1f} {db(rms_r / 32768):9.1f} {db(rms_n / 32768):9.1f} {lag * 1000 / rate:9.2f} "
              f"{db(res / rms_r) if rms_r > 0 else 0:12.1f}")


if __name__ == "__main__":
    main()
