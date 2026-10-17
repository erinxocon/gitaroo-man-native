#!/usr/bin/python3
"""Disassemble a range of an IRX module, annotating jal targets with symbol / import names.
Usage: irxdis.py <irx> <function-name or start-hex> [end-hex]"""
import re
import struct
import subprocess
import sys

THBASE = {4: "CreateThread", 5: "DeleteThread", 6: "StartThread", 10: "TerminateThread", 16: "RotateThreadReadyQueue",
          20: "GetThreadId", 22: "ReferThreadStatus", 24: "SleepThread", 25: "WakeupThread", 33: "DelayThread"}
NAMES = {
    "thbase": THBASE,
    "thsemap": {4: "CreateSema", 5: "DeleteSema", 6: "SignalSema", 7: "iSignalSema", 8: "WaitSema", 9: "PollSema"},
    "vblank": {4: "WaitVblankStart", 5: "WaitVblankEnd", 6: "WaitVblank", 7: "WaitNonVblank", 8: "RegisterVblankHandler", 9: "ReleaseVblankHandler"},
    "cdvdman": {4: "sceCdInit", 5: "sceCdStandby", 6: "sceCdRead", 7: "sceCdSeek", 8: "sceCdGetError", 10: "sceCdSearchFile",
                11: "sceCdSync", 12: "sceCdGetDiskType", 13: "sceCdDiskReady", 28: "sceCdStatus", 37: "sceCdCallback"},
    "ioman": {4: "open", 5: "close", 6: "read", 7: "write", 8: "lseek"},
    "sysclib": {6: "look_ctype", 12: "memcpy", 14: "memset", 22: "strcmp", 23: "strcpy", 29: "strncpy"},
    "sifcmd": {14: "sceSifInitRpc", 17: "sceSifRegisterRpc", 19: "sceSifSetRpcQueue", 22: "sceSifRpcLoop", 23: "sceSifGetNextRequest"},
    "stdio": {4: "printf"},
}


def main():
    path = sys.argv[1]
    data = open(path, "rb").read()
    # section table: find .text file offset and .symtab
    shoff = struct.unpack_from("<I", data, 0x20)[0]
    shnum, shstrndx = struct.unpack_from("<HH", data, 0x30)
    secs = [struct.unpack_from("<IIIIIIIIII", data, shoff + i * 40) for i in range(shnum)]
    strtab_off = secs[shstrndx][4]
    secname = lambda s: data[strtab_off + s[0]:data.index(b"\0", strtab_off + s[0])].decode()
    byname = {secname(s): s for s in secs}
    text_off = byname[".text"][4]
    text_size = byname[".text"][5]
    syms = {}
    sym = byname.get(".symtab")
    if sym:
        str_off = secs[sym[6]][4]
        for i in range(sym[5] // 16):
            name_off, value, size, info, other, shndx = struct.unpack_from("<IIIBBH", data, sym[4] + i * 16)
            if info & 0xF == 2:
                syms[value] = data[str_off + name_off:data.index(b"\0", str_off + name_off)].decode()
    imports = {}
    text = data[text_off:text_off + text_size]
    i = 0
    while i < len(text) - 20:
        if struct.unpack_from("<I", text, i)[0] == 0x41E00000:
            lib = text[i + 12:i + 20].split(b"\0")[0].decode(errors="replace")
            j = i + 20
            while struct.unpack_from("<I", text, j)[0] == 0x03E00008:
                ordinal = struct.unpack_from("<I", text, j + 4)[0] & 0xFFFF
                imports[j] = f"{lib}:{NAMES.get(lib, {}).get(ordinal, ordinal)}"
                j += 8
            i = j
        else:
            i += 4
    target = sys.argv[2]
    if target in syms.values():
        start = next(a for a, n in syms.items() if n == target)
        later = sorted(a for a in syms if a > start)
        end = later[0] if later else text_size
    else:
        start = int(target, 16)
        end = int(sys.argv[3], 16)
    out = subprocess.run(["mips-linux-gnu-objdump", "-D", "-b", "binary", "-m", "mips", "-EL",
                          f"--start-address={start + text_off}", f"--stop-address={end + text_off}", path],
                         capture_output=True, text=True).stdout
    for line in out.splitlines()[7:]:
        m = re.match(r"\s*([0-9a-f]+):\s+([0-9a-f]+)\s+(.*)", line)
        if not m:
            continue
        addr = int(m.group(1), 16) - text_off
        ins = m.group(3)
        jm = re.search(r"jal\s+0x([0-9a-f]+)", ins)
        note = ""
        if jm:
            t = int(jm.group(1), 16) - text_off if int(jm.group(1), 16) >= text_off else int(jm.group(1), 16)
            t2 = int(jm.group(1), 16)
            note = syms.get(t2) or imports.get(t2) or syms.get(t) or imports.get(t) or ""
        label = f"<{syms[addr]}>" if addr in syms else ""
        print(f"{addr:05x}: {ins:40s} {note} {label}")


if __name__ == "__main__":
    main()
