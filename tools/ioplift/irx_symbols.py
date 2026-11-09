#!/usr/bin/env python3
"""Relocated images and symbol lists of Gitaroo's IOP sound modules, for reading them in Ghidra.

For each module in ioplift.MODULES this writes <out>/<NAME>.bin (the IRX relocated at the address
the IOP emulator loads it to) and <out>/<NAME>.sym ("name address" lines for ApplySymbols.java):
the entry point, every import stub (named from the IOP emulator's `case N: // Name` comments where
it has them, else library_ordinal) and, for LIBSD, its sceSd* exports. tools/ioplift/irx_ghidra.sh
imports them into a Ghidra project and dumps pseudo-C.

Usage: python3 tools/ioplift/irx_symbols.py [--iso iso] [-o build/irx]
"""
import argparse
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ioplift  # noqa: E402

ROOT = ioplift.ROOT
EMU = os.path.join(ROOT, "external/PS2Recomp/ps2xIOP/src/emulator")

# Import handler file (or file + dispatch function) -> IRX library name.
HANDLERS = {
    "imports/iop_vblank.cpp": "vblank", "imports/iop_stdio.cpp": "stdio", "imports/iop_heaplib.cpp": "heaplib",
    "imports/iop_ioman.cpp": "ioman", "imports/iop_cdvd.cpp": "cdvdman", "imports/iop_loadcore.cpp": "loadcore",
    "imports/iop_timrman.cpp": "timrman", "imports/iop_sysmem.cpp": "sysmem", "imports/iop_intrman.cpp": "intrman",
    "imports/iop_sysclib.cpp": "sysclib",
}
DISPATCHERS = {
    "dispatchThreadImport": "thbase", "dispatchSemaphoreImport": "thsemap", "dispatchEventImport": "thevent",
    "dispatchSifCmdImport": "sifcmd", "dispatchSifManImport": "sifman",
}

# LIBSD's export table. Ordinal 10 is the one-argument call MSTREAM uses to read a voice's NAX
# (0x2240 | voice), i.e. sceSdGetAddr, which puts sceSdInit at 4.
LIBSD_EXPORTS = {
    2: "sceSdQuit", 4: "sceSdInit", 5: "sceSdSetParam", 6: "sceSdGetParam", 7: "sceSdSetSwitch",
    8: "sceSdGetSwitch", 9: "sceSdSetAddr", 10: "sceSdGetAddr", 11: "sceSdSetCoreAttr", 12: "sceSdGetCoreAttr",
    13: "sceSdNote2Pitch", 14: "sceSdPitch2Note", 15: "sceSdProcBatch", 16: "sceSdProcBatchEx",
    17: "sceSdVoiceTrans", 18: "sceSdBlockTrans", 19: "sceSdVoiceTransStatus", 20: "sceSdBlockTransStatus",
    21: "sceSdSetTransCallback", 22: "sceSdSetIRQCallback", 23: "sceSdSetEffectAttr", 24: "sceSdGetEffectAttr",
    25: "sceSdClearEffectWorkArea", 26: "sceSdSetTransIntrHandler", 27: "sceSdSetSpu2IntrHandler",
}


# Standard ordinals (PS2SDK exports.tab) for what the emulator's handlers do not name.
STANDARD = {
    "thbase": {4: "CreateThread", 5: "DeleteThread", 6: "StartThread", 7: "StartThreadArgs", 8: "ExitThread",
               9: "ExitDeleteThread", 10: "TerminateThread", 11: "iTerminateThread", 14: "ChangeThreadPriority",
               16: "RotateThreadReadyQueue", 18: "ReleaseWaitThread", 20: "GetThreadId", 22: "ReferThreadStatus",
               24: "SleepThread", 25: "WakeupThread", 26: "iWakeupThread", 33: "DelayThread", 34: "GetSystemTime",
               35: "SetAlarm", 36: "iSetAlarm", 37: "CancelAlarm", 38: "iCancelAlarm", 39: "USec2SysClock",
               40: "SysClock2USec"},
    "thsemap": {4: "CreateSema", 5: "DeleteSema", 6: "SignalSema", 7: "iSignalSema", 8: "WaitSema", 9: "PollSema",
                10: "ReferSemaStatus", 11: "iReferSemaStatus"},
    "thevent": {4: "CreateEventFlag", 5: "DeleteEventFlag", 6: "SetEventFlag", 7: "iSetEventFlag",
                8: "ClearEventFlag", 9: "iClearEventFlag", 10: "WaitEventFlag", 11: "PollEventFlag"},
    "ioman": {4: "open", 5: "close", 6: "read", 7: "write", 8: "lseek", 9: "ioctl", 10: "remove", 11: "mkdir",
              12: "rmdir", 13: "dopen", 14: "dclose", 15: "dread", 16: "getstat", 17: "chstat", 18: "format"},
    "loadcore": {4: "FlushDcache", 5: "FlushIcache", 6: "RegisterLibraryEntries", 7: "ReleaseLibraryEntries"},
    "sysclib": {4: "setjmp", 5: "longjmp", 6: "toupper", 7: "tolower", 10: "memchr", 11: "memcmp", 12: "memcpy",
                13: "memmove", 14: "memset", 15: "bcmp", 16: "bcopy", 17: "bzero", 19: "sprintf", 20: "strcat",
                21: "strchr", 22: "strcmp", 23: "strcpy", 27: "strlen", 28: "strncat", 29: "strncmp", 30: "strncpy",
                36: "strtol", 38: "strtoul", 42: "vsprintf"},
    "sifcmd": {4: "sceSifInitCmd", 10: "sceSifAddCmdHandler", 12: "sceSifSendCmd", 13: "isceSifSendCmd",
               14: "sceSifInitRpc", 15: "sceSifBindRpc", 16: "sceSifCallRpc", 17: "sceSifRegisterRpc",
               18: "sceSifCheckStatRpc", 19: "sceSifSetRpcQueue", 20: "sceSifGetNextRequest",
               21: "sceSifExecRequest", 22: "sceSifRpcLoop", 23: "sceSifGetOtherData"},
    "sifman": {7: "sceSifSetDma", 8: "sceSifDmaStat"},
    "libsd": LIBSD_EXPORTS,
}


def import_names():
    names = {}
    case = re.compile(r"case (\d+)u?: *// *([A-Za-z_][A-Za-z0-9_]*)")
    func = re.compile(r"\b(dispatch\w+Import)\s*\(")
    files = set(HANDLERS) | {"core/iop_kernel.cpp", "services/iop_rpc.cpp"}
    for rel in files:
        lib = HANDLERS.get(rel)
        for line in open(os.path.join(EMU, rel)):
            m = func.search(line)
            if m and m.group(1) in DISPATCHERS and "::" in line:
                lib = DISPATCHERS[m.group(1)]
            m = case.search(line)
            if m and lib:
                names.setdefault((lib, int(m.group(1))), m.group(2))
    for lib, table in STANDARD.items():
        for ordinal, label in table.items():
            names.setdefault((lib, ordinal), label)
    return names


def tables(irx, magic):
    """(library, version, [(ordinal, address)]) for each import (0x41E00000) or export (0x41C00000) table."""
    img, out = irx.image, []
    for off in range(0, len(img) - 20, 4):
        if struct.unpack_from("<I", img, off)[0] != magic or struct.unpack_from("<I", img, off + 4)[0] != 0:
            continue
        version = struct.unpack_from("<I", img, off + 8)[0]
        lib = img[off + 12:off + 20].split(b"\0")[0].decode("ascii", "replace")
        entries, p = [], off + 20
        if magic == 0x41E00000:
            # jr ra; addiu zero, zero, ordinal -- until a zero word pair.
            while p + 8 <= len(img) and struct.unpack_from("<I", img, p)[0] == 0x03E00008:
                entries.append((struct.unpack_from("<I", img, p + 4)[0] & 0xFFFF, irx.base + p))
                p += 8
        else:
            ordinal = 0
            while p + 4 <= len(img) and struct.unpack_from("<I", img, p)[0] != 0:
                entries.append((ordinal, struct.unpack_from("<I", img, p)[0]))
                ordinal += 1
                p += 4
        out.append((lib, version, entries))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", default=os.path.join(ROOT, "iso"))
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "build/irx"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    names = import_names()
    for name, rel, base in ioplift.MODULES:
        irx = ioplift.Irx(os.path.join(a.iso, rel), base)
        open(os.path.join(a.out, name + ".bin"), "wb").write(irx.image)
        syms = [(f"{name}_entry", irx.entry)]
        for lib, _, entries in tables(irx, 0x41E00000):
            for ordinal, addr in entries:
                syms.append((names.get((lib, ordinal), f"{lib}_{ordinal}"), addr))
        for lib, _, entries in tables(irx, 0x41C00000):
            for ordinal, addr in entries:
                if ordinal >= 2 and addr:
                    label = LIBSD_EXPORTS.get(ordinal) if lib == "libsd" else None
                    syms.append((label or f"{lib}_export_{ordinal}", addr))
        # Code reached through pointers (thread entries, RPC and interrupt handlers) for analysis
        # to start from.
        named = {addr for _, addr in syms}
        syms += [(f"ptr_{addr:05x}", addr) for addr in sorted(irx.code_pointers) if addr not in named]
        with open(os.path.join(a.out, name + ".sym"), "w") as f:
            for label, addr in syms:
                f.write(f"{label} 0x{addr:08X}\n")
        print(f"{name}: base 0x{base:x}, {len(irx.image)} bytes, {len(syms)} symbols")


if __name__ == "__main__":
    main()
