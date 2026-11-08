#!/usr/bin/env python3
"""Inspect IMC sound libraries (MSTREAM's format, docs/native-audio.md) and decode streams to WAV.

  python3 tools/audio/imc.py list <file.IMC>             entries and stream headers
  python3 tools/audio/imc.py wav <file.IMC> <NAME> <out.wav>  decode one stream (PS-ADPCM) to WAV

The game's data: never commit decoded output.
"""
import struct
import sys
import wave

# PS-ADPCM prediction filters (the SPU's, in 1/64ths).
FILTERS = [(0, 0), (60, 0), (115, -52), (98, -55), (122, -60)]


def entries(data):
    count, = struct.unpack_from("<I", data, 0)
    out = []
    for i in range(count):
        e = data[4 + 32 * i:4 + 32 * (i + 1)]
        name = e[:9].split(b"\0")[0].decode("ascii", "replace")  # 8-character names end at byte 8
        words = struct.unpack_from("<6I", e, 8)
        out.append((name, words))
    return out


def header(data, offset):
    return struct.unpack_from("<4I", data, offset)


def stream_end(data, ents, index):
    offsets = sorted(w[2] for _, w in ents)
    off = ents[index][1][2]
    later = [o for o in offsets if o > off]
    return later[0] if later else len(data)


def decode_channel(blocks):
    """PS-ADPCM blocks (16 bytes each) -> list of int16 samples; stops at an end flag."""
    out, h1, h2 = [], 0, 0
    for b in range(0, len(blocks) - 15, 16):
        shift, filt = blocks[b] & 0x0F, min(blocks[b] >> 4, 4)
        flags = blocks[b + 1]
        f0, f1 = FILTERS[filt]
        for i in range(28):
            byte = blocks[b + 2 + i // 2]
            nib = (byte >> (4 * (i & 1))) & 0x0F
            s = (nib << 12) & 0xFFFF
            if s & 0x8000:
                s -= 0x10000
            s >>= shift if shift <= 12 else 12
            s += (h1 * f0 + h2 * f1 + 32) >> 6
            s = max(-32768, min(32767, s))
            out.append(s)
            h2, h1 = h1, s
        if flags & 1 and not flags & 2:  # end without loop
            break
    return out


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, path = sys.argv[1], sys.argv[2]
    data = open(path, "rb").read()
    ents = entries(data)
    if cmd == "list":
        for i, (name, w) in enumerate(ents):
            ch, rate, inter, fourth = header(data, w[2])
            size = stream_end(data, ents, i) - w[2] - 16
            secs = size / ch / 16 * 28 / rate if ch and rate else 0
            print(f"{i:3} {name:8} off=0x{w[2]:08x} ch={ch} rate={rate} interleave=0x{inter:x} w4={fourth} "
                  f"bytes={size} ~{secs:.1f}s entry={' '.join(f'{x:08x}' for x in w)}")
    elif cmd == "wav":
        name, outp = sys.argv[3].upper(), sys.argv[4]
        i = next(k for k, (n, _) in enumerate(ents) if n == name)
        off = ents[i][1][2]
        ch, rate, inter, _ = header(data, off)
        body = data[off + 16:stream_end(data, ents, i)]
        # Channels alternate in chunks of `interleave` 16-byte blocks (MSTREAM's SPU buffer size).
        chunk = inter * 16
        chans = [bytearray() for _ in range(ch)]
        for c0 in range(0, len(body) - chunk * ch + 1, chunk * ch):
            for c in range(ch):
                chans[c] += body[c0 + c * chunk:c0 + (c + 1) * chunk]
        pcm = [decode_channel(bytes(c)) for c in chans]
        n = min(len(p) for p in pcm)
        with wave.open(outp, "wb") as w:
            w.setnchannels(ch)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"".join(struct.pack(f"<{ch}h", *(p[k] for p in pcm)) for k in range(n)))
        print(f"{name}: {ch} channels, {rate} Hz, {n} samples ({n / rate:.1f} s) -> {outp}")


if __name__ == "__main__":
    main()
