#!/usr/bin/env python3
"""List or extract the files of a PS2 memory card image (PCSX2's raw Mcd00N.ps2 format).

The port's memory card is a host directory (mc0/ next to the ELF, e.g. iso/mc0/), so a save made
in PCSX2 can be carried over by extracting its directory there:

  python3 tools/pcsx2/mcextract.py ~/.var/app/net.pcsx2.PCSX2/config/PCSX2/memcards/Mcd001.ps2
  python3 tools/pcsx2/mcextract.py <card> --extract BASLUS-20294 --to iso/mc0

Saves are personal data: never commit them.
"""
import argparse
import os
import struct
import sys

MAGIC = b"Sony PS2 Memory Card Format "


class Card:
    def __init__(self, path):
        self.raw = open(path, "rb").read()
        if not self.raw.startswith(MAGIC):
            raise SystemExit("%s: not a PS2 memory card image" % path)
        (self.page_len, self.pages_per_cluster, self.pages_per_block, _, self.clusters_per_card,
         self.alloc_offset, self.alloc_end, self.rootdir_cluster) = struct.unpack_from("<HHHHIIII", self.raw, 0x28)
        self.ifc_list = struct.unpack_from("<32I", self.raw, 0x50)
        # Images with ECC store 16 spare bytes after each 512-byte page.
        pages = self.clusters_per_card * self.pages_per_cluster
        self.stride = len(self.raw) // pages
        if self.stride not in (self.page_len, self.page_len + 16):
            raise SystemExit("unexpected image size %d for %d pages" % (len(self.raw), pages))
        self.cluster_len = self.page_len * self.pages_per_cluster
        self.fat_per_cluster = self.cluster_len // 4

    def cluster(self, n):
        out = bytearray()
        for p in range(self.pages_per_cluster):
            at = (n * self.pages_per_cluster + p) * self.stride
            out += self.raw[at:at + self.page_len]
        return bytes(out)

    def fat_entry(self, n):
        per = self.fat_per_cluster
        indirect = struct.unpack_from("<I", self.cluster(self.ifc_list[n // per // per]), (n // per % per) * 4)[0]
        return struct.unpack_from("<I", self.cluster(indirect), (n % per) * 4)[0]

    def chain(self, first):
        n = first
        while True:
            yield n
            e = self.fat_entry(n)
            if e == 0xFFFFFFFF or not e & 0x80000000:
                return
            n = e & 0x7FFFFFFF

    def read(self, first, length):
        out = bytearray()
        for n in self.chain(first):
            out += self.cluster(n + self.alloc_offset)
            if len(out) >= length:
                break
        return bytes(out[:length])

    def entries(self, first, count):
        data = self.read(first, count * 512)
        for i in range(count):
            e = data[i * 512:(i + 1) * 512]
            mode, length = struct.unpack_from("<HxxI", e, 0)
            cluster = struct.unpack_from("<I", e, 0x10)[0]
            name = e[0x40:0x60].split(b"\0")[0].decode("ascii", "replace")
            if mode & 0x8000 and name not in (".", ".."):
                yield name, mode, length, cluster

    def root(self):
        root = self.read(self.rootdir_cluster, 512)
        count = struct.unpack_from("<I", root, 4)[0]
        return self.entries(self.rootdir_cluster, count)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("card")
    ap.add_argument("--extract", metavar="DIR", help="save directory to extract")
    ap.add_argument("--to", default=".", help="host directory to extract into (e.g. iso/mc0)")
    a = ap.parse_args(argv)
    card = Card(a.card)
    for name, mode, length, cluster in card.root():
        if not mode & 0x20:
            continue
        files = list(card.entries(cluster, length))
        if a.extract is None:
            print("%-32s %d files" % (name, len(files)))
            for fname, fmode, flen, _ in files:
                print("    %-28s %8d" % (fname, flen))
        elif name == a.extract:
            dest = os.path.join(a.to, name)
            os.makedirs(dest, exist_ok=True)
            for fname, fmode, flen, fcluster in files:
                if fmode & 0x10:
                    with open(os.path.join(dest, fname), "wb") as f:
                        f.write(card.read(fcluster, flen))
            print("extracted %d files to %s" % (len(files), dest))
            return
    if a.extract is not None:
        sys.exit("no directory %s on the card" % a.extract)


if __name__ == "__main__":
    main()
