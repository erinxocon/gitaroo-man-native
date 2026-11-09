#!/usr/bin/env python3
"""Read the game ELF's memory image by virtual address (for tables, strings and constants).

  python3 tools/analysis/elfmem.py words <addr> <count>     u32 words
  python3 tools/analysis/elfmem.py floats <addr> <count>    floats
  python3 tools/analysis/elfmem.py ptmf <addr> <count>      CodeWarrior pointer-to-member entries (delta, vindex, func)
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Elf:
    def __init__(self, path=os.path.join(ROOT, "iso/SLUS_202.94")):
        self.data = open(path, "rb").read()
        phoff, = struct.unpack_from("<I", self.data, 0x1C)
        phentsize, phnum = struct.unpack_from("<HH", self.data, 0x2A)
        self.segments = []
        for i in range(phnum):
            p_type, off, vaddr, _, filesz, memsz = struct.unpack_from("<6I", self.data, phoff + i * phentsize)
            if p_type == 1:
                self.segments.append((vaddr, memsz, off, filesz))

    def read(self, addr, size):
        for vaddr, memsz, off, filesz in self.segments:
            if vaddr <= addr < vaddr + memsz:
                rel = addr - vaddr
                chunk = self.data[off + rel:off + min(filesz, rel + size)]
                return chunk + bytes(size - len(chunk))
        raise ValueError(f"0x{addr:x} not in the image")

    def u32(self, addr):
        return struct.unpack("<I", self.read(addr, 4))[0]


def main():
    cmd, addr, count = sys.argv[1], int(sys.argv[2], 0), int(sys.argv[3], 0)
    elf = Elf()
    if cmd == "words":
        for i in range(count):
            print(f"{addr + 4 * i:08x}: {elf.u32(addr + 4 * i):08x}")
    elif cmd == "floats":
        for i in range(count):
            print(f"{addr + 4 * i:08x}: {struct.unpack('<f', elf.read(addr + 4 * i, 4))[0]}")
    elif cmd == "ptmf":
        for i in range(count):
            d, v, f = struct.unpack("<iiI", elf.read(addr + 12 * i, 12))
            print(f"{i:3} (0x{i:02x}): delta={d} vindex={v} func=0x{f:x}")


if __name__ == "__main__":
    main()
