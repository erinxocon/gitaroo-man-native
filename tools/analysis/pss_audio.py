#!/usr/bin/python3
"""Extract the PCM audio (private stream 1, SShd/SSbd) from a PSS movie as a raw int16 stream."""
import struct
import sys


def main():
    data = open(sys.argv[1], "rb").read()
    out = bytearray()
    i = 0
    while i + 6 < len(data):
        if data[i:i + 3] != b"\x00\x00\x01":
            i += 1
            continue
        sid = data[i + 3]
        if sid == 0xBA:  # pack header (MPEG-2)
            stuffing = data[i + 13] & 7
            i += 14 + stuffing
            continue
        if sid == 0xB9:
            break
        length = struct.unpack(">H", data[i + 4:i + 6])[0]
        if sid == 0xBD:
            header_len = data[i + 8]
            payload = data[i + 9 + header_len:i + 6 + length]
            out += payload[4:]  # skip sub-stream id word
        i += 6 + length
    # Strip the SShd (8 + 24 bytes) and SSbd (8 bytes) headers.
    pos = out.find(b"SSbd")
    body = out[pos + 8:] if pos >= 0 else out
    print("header", out[:40].hex(), file=sys.stderr)
    open(sys.argv[2], "wb").write(body)


if __name__ == "__main__":
    main()
