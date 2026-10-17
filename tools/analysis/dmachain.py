#!/usr/bin/python3
"""Walk a PS2 source-chain DMA list in a RAM dump and print tags (and GIF tags inside payloads)."""
import struct
import sys

IDS = ["refe", "cnt", "next", "ref", "refs", "call", "ret", "end"]


def main():
    ram = open(sys.argv[1], "rb").read()
    tadr = int(sys.argv[2], 0)
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 64
    for _ in range(limit):
        lo, hi = struct.unpack_from("<QQ", ram, tadr & 0x1FFFFFF)
        qwc = lo & 0xFFFF
        tid = (lo >> 28) & 7
        addr = (lo >> 32) & 0x7FFFFFFF
        print(f"tag@{tadr:08x} {IDS[tid]:4} qwc={qwc:#x} addr={addr:08x} hi={hi:016x}")
        data = tadr + 16 if tid in (1, 2, 5, 6, 7) else addr
        if tid in (1, 2, 7) and qwc:
            g0, g1 = struct.unpack_from("<QQ", ram, data & 0x1FFFFFF)
            print(f"    first qw {g0:016x} {g1:016x} (gif nloop={g0 & 0x7fff} flg={(g0 >> 58) & 3} nreg={(g0 >> 60) & 0xf})")
        if tid == 1:
            tadr = data + qwc * 16
        elif tid == 2:
            tadr = addr
        elif tid in (3, 4):
            tadr += 16
        else:
            break


if __name__ == "__main__":
    main()
