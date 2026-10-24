#!/usr/bin/env python3
"""PINE client for a running PCSX2 (start it with tools/pcsx2/pcsx2.sh, PINE enabled).

  pine.py info                       version / status / game id
  pine.py read <addr> [len]          hexdump EE memory (live)
  pine.py dump <out.bin> [addr len]  raw EE memory to a file (live, not a consistent snapshot)
  pine.py poll <addr> [n] [secs]     print a u32 every <secs> (watch a state variable)
  pine.py state <outdir> [slot]      save state through PINE, then unpack the .p2s members
                                     (eeMemory.bin, vu1MicroMem.bin, vu1Memory.bin, ...) into outdir
  pine.py collect <outdir>           (no PINE needed) archive + unpack every save state PCSX2 writes
                                     as outdir/NNN/, so one slot can be reused forever (F1 only)

Socket: $PCSX2_PINE_SOCK, else <runtime>/app/net.pcsx2.PCSX2/pcsx2.sock, else <runtime>/pcsx2.sock.
"""
import glob
import os
import socket
import struct
import subprocess
import sys
import time
import zlib

READ8, READ16, READ32, READ64 = 0, 1, 2, 3
VERSION, SAVESTATE, LOADSTATE, TITLE, ID, UUID, GAMEVER, STATUS = 8, 9, 10, 11, 12, 13, 14, 15
MAX_READS = 50000  # 8 bytes each stays under PINE's 450000-byte reply limit
SSTATES = os.path.expanduser("~/.var/app/net.pcsx2.PCSX2/config/PCSX2/sstates")


def sock_path():
    if os.environ.get("PCSX2_PINE_SOCK"):
        return os.environ["PCSX2_PINE_SOCK"]
    run = os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
    for p in (os.path.join(run, "app/net.pcsx2.PCSX2/pcsx2.sock"), os.path.join(run, "pcsx2.sock")):
        if os.path.exists(p):
            return p
    raise SystemExit("no PINE socket found -- is PCSX2 running via pcsx2.sh with "
                     "Settings > Advanced > PINE enabled?")


class Pine:
    def __init__(self):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(sock_path())

    def call(self, payload):
        self.s.sendall(struct.pack("<I", len(payload) + 4) + payload)
        hdr = self._recv(4)
        body = self._recv(struct.unpack("<I", hdr)[0] - 4)
        if body[0] != 0:
            raise RuntimeError("PINE command failed")
        return body[1:]

    def _recv(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.s.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("PINE socket closed")
            buf += chunk
        return bytes(buf)

    def string(self, op):
        r = self.call(bytes([op]))
        n = struct.unpack_from("<I", r)[0]
        return r[4:4 + n].rstrip(b"\0").decode(errors="replace")

    def u32(self, addr):
        return struct.unpack("<I", self.call(struct.pack("<BI", READ32, addr)))[0]

    def read(self, addr, size):
        out = bytearray()
        end = addr + ((size + 7) & ~7)
        while addr < end:
            n = min(MAX_READS, (end - addr) // 8)
            out += self.call(b"".join(struct.pack("<BI", READ64, addr + 8 * i) for i in range(n)))
            addr += 8 * n
        return bytes(out[:size])


def unpack_p2s(path, outdir):
    """A .p2s is a zip; PCSX2's default per-member compression is zstd (method 93)."""
    data = open(path, "rb").read()
    eocd = data.rfind(b"PK\x05\x06")
    count, _, cd_off = struct.unpack_from("<HII", data, eocd + 10)
    os.makedirs(outdir, exist_ok=True)
    pos = cd_off
    for _ in range(count):
        method, = struct.unpack_from("<H", data, pos + 10)
        csize, usize, nlen, xlen, clen = struct.unpack_from("<IIHHH", data, pos + 20)
        loff, = struct.unpack_from("<I", data, pos + 42)
        name = data[pos + 46:pos + 46 + nlen].decode()
        pos += 46 + nlen + xlen + clen
        lnlen, lxlen = struct.unpack_from("<HH", data, loff + 26)
        raw = data[loff + 30 + lnlen + lxlen:loff + 30 + lnlen + lxlen + csize]
        if method == 0:
            blob = raw
        elif method == 8:
            blob = zlib.decompress(raw, -15)
        elif method == 93:
            blob = subprocess.run(["zstd", "-dc"], input=raw, capture_output=True, check=True).stdout
        else:
            print("  skip %s (zip method %d)" % (name, method))
            continue
        dst = os.path.join(outdir, os.path.basename(name))
        open(dst, "wb").write(blob)
        print("  %-28s %9d bytes" % (name, len(blob)))


def collect(outdir):
    """Poll the sstates dir; each time a slot file is (re)written, copy and unpack it as the next NNN."""
    os.makedirs(outdir, exist_ok=True)
    seen = {f: os.path.getmtime(f) for f in glob.glob(os.path.join(SSTATES, "*.p2s"))}
    seq = len(glob.glob(os.path.join(outdir, "[0-9][0-9][0-9]")))
    print("watching %s -> %s (next #%03d)" % (SSTATES, outdir, seq + 1), flush=True)
    while True:
        for f in glob.glob(os.path.join(SSTATES, "*.p2s")):
            mtime = os.path.getmtime(f)
            if seen.get(f) == mtime:
                continue
            size = -1
            while size != os.path.getsize(f):  # wait for the writer to finish
                size = os.path.getsize(f)
                time.sleep(0.5)
            seen[f] = os.path.getmtime(f)
            seq += 1
            dst = os.path.join(outdir, "%03d" % seq)
            os.makedirs(dst, exist_ok=True)
            with open(f, "rb") as src, open(dst + ".p2s", "wb") as out:
                out.write(src.read())
            unpack_p2s(dst + ".p2s", dst)
            with open(os.path.join(outdir, "index.tsv"), "a") as idx:
                idx.write("%03d\t%s\t%s\n" % (seq, time.strftime("%H:%M:%S", time.localtime(seen[f])),
                                              os.path.basename(f)))
            print("#%03d <- %s" % (seq, os.path.basename(f)), flush=True)
        time.sleep(1.0)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd, args = argv[1], argv[2:]
    if cmd == "collect":
        return collect(args[0])
    p = Pine()
    if cmd == "info":
        status = struct.unpack("<I", p.call(bytes([STATUS])))[0]
        print("version :", p.string(VERSION))
        print("status  :", {0: "running", 1: "paused", 2: "shutdown"}.get(status, status))
        if status != 2:
            print("title   :", p.string(TITLE))
            print("id      :", p.string(ID))
            print("crc     :", p.string(UUID))
            print("gamever :", p.string(GAMEVER))
    elif cmd == "read":
        addr = int(args[0], 0)
        data = p.read(addr, int(args[1], 0) if len(args) > 1 else 256)
        for i in range(0, len(data), 16):
            row = data[i:i + 16]
            print("%08X  %-48s %s" % (addr + i, " ".join("%02X" % b for b in row),
                                      "".join(chr(b) if 32 <= b < 127 else "." for b in row)))
    elif cmd == "dump":
        addr = int(args[1], 0) if len(args) > 1 else 0
        size = int(args[2], 0) if len(args) > 2 else 32 << 20
        t = time.time()
        data = p.read(addr, size)
        open(args[0], "wb").write(data)
        print("wrote %s: %d bytes from %08X in %.1fs" % (args[0], len(data), addr, time.time() - t))
    elif cmd == "poll":
        addr = int(args[0], 0)
        n = int(args[1]) if len(args) > 1 else 0
        secs = float(args[2]) if len(args) > 2 else 0.5
        last, i = None, 0
        while not n or i < n:
            v = p.u32(addr)
            if v != last:
                print("%.2f  [%08X] = %08X (%d)" % (time.time(), addr, v, v), flush=True)
                last = v
            i += 1
            time.sleep(secs)
    elif cmd == "state":
        slot = int(args[1]) if len(args) > 1 else 10
        before = time.time()
        p.call(struct.pack("<BB", SAVESTATE, slot))
        pattern = os.path.join(SSTATES, "*.%02d.p2s" % slot)
        for _ in range(100):
            hits = [f for f in glob.glob(pattern) if os.path.getmtime(f) >= before]
            if hits:
                time.sleep(0.5)  # let the writer finish
                print("unpacking", hits[0])
                unpack_p2s(hits[0], args[0])
                return 0
            time.sleep(0.2)
        raise SystemExit("save state did not appear under %s" % pattern)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
