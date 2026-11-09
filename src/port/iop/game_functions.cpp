// What the game still asked of the PS2's I/O processor, defined natively so that nothing runs on
// the IOP emulator. The pad, memory card and CD functions never reach it: they are already host
// code. The port's build leaves the recompiled versions of these out (GITAROO_NATIVE_FUNCTIONS in
// CMakeLists.txt).

#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"

#include <ps2_recompiled_functions.h>
#include <ps2_recompiled_stubs.h>

#include <cstring>

namespace
{
    void returnValue(R5900Context *ctx, int32_t value)
    {
        SET_GPR_S32(ctx, 2, value);
        ctx->pc = GPR_U32(ctx, 31);
    }
}

// Loads one IOP driver module for Iop_LoadModules (which also sets up the CD and the main thread's
// priority, so it stays recompiled); a result below zero means retry. No module is needed any more.
void FUN_00108e80_0x108e80(uint8_t *, R5900Context *ctx, PS2Runtime *)
{
    returnValue(ctx, 1);
}

void sceSifRebootIop_0x131af0(uint8_t *, R5900Context *ctx, PS2Runtime *)
{
    returnValue(ctx, 1);
}

void sceSifSyncIop_0x131aa8(uint8_t *, R5900Context *ctx, PS2Runtime *)
{
    returnValue(ctx, 1);
}

void sceSifInitRpc_0x12f1e0(uint8_t *, R5900Context *ctx, PS2Runtime *)
{
    returnValue(ctx, 0);
}

void sceSifInitIopHeap_0x130e20(uint8_t *, R5900Context *ctx, PS2Runtime *)
{
    returnValue(ctx, 0);
}

void sceSifLoadFileReset_0x1311e8(uint8_t *, R5900Context *ctx, PS2Runtime *)
{
    returnValue(ctx, 0);
}

// The only allocation left is the movie player's CD stream buffer, which sceCdStInit only needs to
// be non-zero: the stream is served by the host.
void sceSifAllocIopHeap_0x130ea8(uint8_t *, R5900Context *ctx, PS2Runtime *)
{
    returnValue(ctx, 0x00100000);
}

void sceSifFreeIopHeap_0x130f18(uint8_t *, R5900Context *ctx, PS2Runtime *)
{
    returnValue(ctx, 0);
}

// The game waits for the client's server pointer to be set; nothing else about a binding is used,
// since no request is sent over it.
void sceSifBindRpc_0x12f870(uint8_t *rdram, R5900Context *ctx, PS2Runtime *)
{
    const uint32_t server = 1u;
    if (uint8_t *p = getMemPtr(rdram, GPR_U32(ctx, 4) + 0x24u))
        std::memcpy(p, &server, sizeof(server));
    returnValue(ctx, 0);
}

// Multitap: none is connected, as on a console without one.
void sceMtapInit_0x135d38(uint8_t *, R5900Context *ctx, PS2Runtime *)
{
    returnValue(ctx, 1);
}

void sceMtapPortOpen_0x135fb8(uint8_t *, R5900Context *ctx, PS2Runtime *)
{
    returnValue(ctx, 1);
}

void sceMtapGetConnection_0x136098(uint8_t *, R5900Context *ctx, PS2Runtime *)
{
    returnValue(ctx, 0);
}
