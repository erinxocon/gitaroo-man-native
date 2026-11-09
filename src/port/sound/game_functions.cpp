// The game's own sound functions, defined natively. The port's build leaves the recompiled versions
// out (GITAROO_NATIVE_FUNCTIONS in CMakeLists.txt), so every call reaches these.

#include "sound_engine.h"

#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"
#include "ps2_stubs.h"
#include "ps2_syscalls.h"
#include "runtime/ee_scheduler.h"

#include <ps2_recompiled_functions.h>
#include <ps2_recompiled_stubs.h>

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

namespace
{
    constexpr uint32_t kMstreamSid = 0x1563u;
    constexpr uint32_t kMstreamClient = 0x0029D4C0u;
    constexpr uint32_t kMoviePlayerVolume = 0x002649BCu;

    void returnValue(R5900Context *ctx, int32_t value)
    {
        SET_GPR_S32(ctx, 2, value);
        ctx->pc = GPR_U32(ctx, 31);
    }

    uint32_t load32(uint8_t *rdram, uint32_t address)
    {
        uint32_t value = 0;
        if (const uint8_t *p = getConstMemPtr(rdram, address))
            std::memcpy(&value, p, 4);
        return value;
    }

    void store32(uint8_t *rdram, uint32_t address, uint32_t value)
    {
        if (uint8_t *p = getMemPtr(rdram, address))
            std::memcpy(p, &value, 4);
    }

    // The fields of the movie player's audio decoder (word indices).
    enum AudioDec : uint32_t
    {
        kState = 0x0,
        kHeaderBytes = 0xB,
        kData = 0xC,
        kPut = 0xD,
        kCount = 0xE,
        kSize = 0xF,
        kTotalPut = 0x10,
        kPcmRing = 0x11,
        kPcmRingSize = 0x12,
        kPcmLast = 0x13,
        kPcmPause = 0x14,
        kPcmTotal = 0x15,
        kZeroBlock = 0x16,
    };
    enum AudioState : uint32_t
    {
        kInit = 0,
        kPreset = 1,
        kPlay = 2,
        kPause = 3,
    };

    uint32_t field(uint8_t *rdram, uint32_t ad, AudioDec f)
    {
        return load32(rdram, ad + f * 4u);
    }

    void setField(uint8_t *rdram, uint32_t ad, AudioDec f, uint32_t value)
    {
        store32(rdram, ad + f * 4u, value);
    }
}

// ---------------------------------------------------------------------------------------------
// The driver modules are not loaded; their server is not bound.
// ---------------------------------------------------------------------------------------------

void _sceSifLoadModule_0x131410(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
{
    std::string path;
    if (const char *p = reinterpret_cast<const char *>(getConstMemPtr(rdram, GPR_U32(ctx, 4))))
        path = p;
    std::transform(path.begin(), path.end(), path.begin(), [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
    for (const char *module : {"LIBSD.IRX", "SDRDRV.IRX", "MSTREAM.IRX"})
    {
        if (path.find(module) != std::string::npos)
        {
            static int32_t nextId = 0x7000;
            returnValue(ctx, nextId++);
            return;
        }
    }
    ctx->pc = GPR_U32(ctx, 31);
    ps2_syscalls::sceSifLoadModule(rdram, ctx, runtime);
}

void sceSifBindRpc_0x12f870(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
{
    if (GPR_U32(ctx, 5) == kMstreamSid)
    {
        // The game waits for the client's server pointer to be set.
        store32(rdram, GPR_U32(ctx, 4) + 0x24u, 1u);
        returnValue(ctx, 0);
        return;
    }
    ctx->pc = GPR_U32(ctx, 31);
    ps2_stubs::sceSifBindRpc(rdram, ctx, runtime);
}

// ---------------------------------------------------------------------------------------------
// The game's one function for talking to the sound driver: (request, buffers, nowait), with
// fixed send and reply buffers per `buffers`.
// ---------------------------------------------------------------------------------------------

void FUN_0023e690_0x23e690(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
{
    const uint32_t fno = GPR_U32(ctx, 4);
    uint32_t send = 0x0029D580u, sendSize = 0x40u, reply = 0x0029D600u, replySize = 0x10u;
    switch (GPR_U32(ctx, 5) & 0xFFu)
    {
    case 1: send = 0; sendSize = 0; break;
    case 2: send = 0; sendSize = 0; reply = 0x0029D640u; replySize = 0x40u; break;
    case 3: send = 0x0029D500u; break;
    case 4: send = 0x0029D540u; sendSize = 0x10u; break;
    case 6: send = 0x0029D5C0u; sendSize = 0x10u; break;
    default: break;
    }
    uint8_t answer[16];
    const uint8_t *request = send ? getConstMemPtr(rdram, send) : nullptr;
    port::sound::request(*runtime, fno, request, sendSize, answer);
    if (uint8_t *out = getMemPtr(rdram, reply))
    {
        std::memset(out, 0, replySize);
        std::memcpy(out, answer, 16u);
    }

    // PS2X_TRACE_MSTREAM=1: the same lines the emulated reference prints (tools/audio/mstream_diff.py).
    static const bool s_trace = std::getenv("PS2X_TRACE_MSTREAM") != nullptr;
    if (s_trace)
    {
        uint32_t sw[4] = {}, rw[4];
        if (request)
            std::memcpy(sw, request, std::min<size_t>(sendSize, sizeof(sw)));
        std::memcpy(rw, answer, sizeof(rw));
        static const auto s_start = std::chrono::steady_clock::now();
        const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - s_start).count();
        std::fprintf(stderr, "[mstream] t=%.1f tick=%llu fno=0x%x send=%08x %08x %08x %08x recv=%08x %08x %08x %08x\n", ms,
                     static_cast<unsigned long long>(runtime->eeScheduler().currentVSyncTick()), fno, sw[0], sw[1], sw[2], sw[3],
                     rw[0], rw[1], rw[2], rw[3]);
    }
    returnValue(ctx, static_cast<int32_t>(load32(rdram, kMstreamClient + 4u)));
}

// ---------------------------------------------------------------------------------------------
// Sony's library for the sound driver, only used by the movie player below.
// ---------------------------------------------------------------------------------------------

void sceSdRemoteInit_0x1361e0(uint8_t *, R5900Context *ctx, PS2Runtime *)
{
    returnValue(ctx, 0);
}

void sceSdRemote_0x136320(uint8_t *, R5900Context *ctx, PS2Runtime *)
{
    returnValue(ctx, 0);
}

// ---------------------------------------------------------------------------------------------
// The movie player's audio decoder (Sony's ezmpeg sample): it collects PCM in a ring on the EE and
// sends it on. The PS2 sends it to a ring in IOP memory the sound chip reads by DMA; here it goes
// to the mixer.
// ---------------------------------------------------------------------------------------------

// audioDecCreate(ad, data, size, pcmRingSize)
void FUN_0023f660_0x23f660(uint8_t *rdram, R5900Context *ctx, PS2Runtime *)
{
    const uint32_t ad = GPR_U32(ctx, 4);
    for (AudioDec f : {kState, kHeaderBytes, kPut, kCount, kTotalPut, kPcmRing, kPcmLast, kPcmPause, kPcmTotal, kZeroBlock})
        setField(rdram, ad, f, 0u);
    setField(rdram, ad, kData, GPR_U32(ctx, 5));
    setField(rdram, ad, kSize, GPR_U32(ctx, 6));
    setField(rdram, ad, kPcmRingSize, GPR_U32(ctx, 7));
    port::sound::movieOpen(GPR_U32(ctx, 7));
    port::sound::setMaster(static_cast<uint16_t>(load32(rdram, kMoviePlayerVolume)));
    returnValue(ctx, 1);
}

// audioDecDelete(ad)
void FUN_0023f5e0_0x23f5e0(uint8_t *, R5900Context *ctx, PS2Runtime *)
{
    port::sound::moviePlay(false);
    port::sound::setMaster(0u);
    returnValue(ctx, 1);
}

// audioDecStart(ad)
void FUN_0023f4b0_0x23f4b0(uint8_t *rdram, R5900Context *ctx, PS2Runtime *)
{
    port::sound::moviePlay(true);
    setField(rdram, GPR_U32(ctx, 4), kState, kPlay);
    ctx->pc = GPR_U32(ctx, 31);
}

// audioDecPause(ad)
void FUN_0023f540_0x23f540(uint8_t *rdram, R5900Context *ctx, PS2Runtime *)
{
    setField(rdram, GPR_U32(ctx, 4), kState, kPause);
    port::sound::moviePlay(false);
    ctx->pc = GPR_U32(ctx, 31);
}

// audioDecReset(ad)
void FUN_0023f3f0_0x23f3f0(uint8_t *rdram, R5900Context *ctx, PS2Runtime *)
{
    const uint32_t ad = GPR_U32(ctx, 4);
    port::sound::moviePlay(false);
    port::sound::movieClear();
    for (AudioDec f : {kState, kHeaderBytes, kPut, kCount, kTotalPut, kPcmLast, kPcmPause, kPcmTotal})
        setField(rdram, ad, f, 0u);
    ctx->pc = GPR_U32(ctx, 31);
}

// audioDecSendToIOP(ad): moves whole 0x400-byte blocks of PCM from the EE ring on; returns the bytes sent.
void FUN_0023eff0_0x23eff0(uint8_t *rdram, R5900Context *ctx, PS2Runtime *)
{
    const uint32_t ad = GPR_U32(ctx, 4);
    uint32_t room = 0;
    switch (field(rdram, ad, kState))
    {
    case kPreset:
        room = field(rdram, ad, kPcmRingSize) - field(rdram, ad, kPcmTotal);
        break;
    case kPlay:
        room = port::sound::movieFree();
        break;
    default:
        returnValue(ctx, 0);
        return;
    }
    const uint32_t count = field(rdram, ad, kCount);
    const uint32_t bytes = std::min(room & ~0x3FFu, count & ~0x3FFu);
    if (bytes < 0x400u)
    {
        returnValue(ctx, 0);
        return;
    }
    const uint32_t data = field(rdram, ad, kData), size = field(rdram, ad, kSize);
    const uint32_t read = (field(rdram, ad, kPut) + size - count) % size;
    const uint32_t first = std::min(bytes, size - read);
    if (const uint8_t *p = getConstMemPtr(rdram, data + read))
        port::sound::moviePut(p, first);
    if (bytes > first)
        if (const uint8_t *p = getConstMemPtr(rdram, data))
            port::sound::moviePut(p, bytes - first);
    setField(rdram, ad, kCount, count - bytes);
    setField(rdram, ad, kPcmTotal, field(rdram, ad, kPcmTotal) + bytes);
    setField(rdram, ad, kPcmLast, (field(rdram, ad, kPcmLast) + bytes) % field(rdram, ad, kPcmRingSize));
    returnValue(ctx, static_cast<int32_t>(bytes));
}
