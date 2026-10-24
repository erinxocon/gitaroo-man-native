// Gitaroo Man (SLUS-202.94) specific runtime overrides.
//
// Each override replaces one guest function by address. Keep them minimal and document why the
// original code cannot run as-is under the recompiled runtime.

#include "game_overrides.h"
#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace
{
    constexpr uint32_t kGitarooEntry = 0x00100008u;

    // gmMovie: videoDecSendEndCode(VideoDec *vd) @ 0x242640.
    // Appends a sequence_end_code to the IPU input ring (viBuf at vd+0x48) and marks the decoder as
    // ending (vd->state 0 -> 2). On hardware the IPU DMA drains that ring; with HLE MPEG decoding
    // nothing does, so it is full by the end of every movie and the caller would spin forever.
    // The HLE decoder detects end of stream on its own, so only the state change is needed.
    void gmMovieSendEndCode(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        (void)runtime;
        const uint32_t videoDec = GPR_U32(ctx, 4) & 0x01FFFFFFu;
        uint32_t state = 0u;
        std::memcpy(&state, rdram + videoDec + 0xA8u, sizeof(state));
        if (state == 0u)
        {
            state = 2u;
            std::memcpy(rdram + videoDec + 0xA8u, &state, sizeof(state));
        }
        SET_GPR_U32(ctx, 2, 1u);
        ctx->pc = GPR_U32(ctx, 31);
    }

    // gmMovie: main streaming loop @ 0x240530 (returns 1 when playback finished). With
    // PS2X_SKIP_MOVIES set it returns immediately so the normal teardown runs; testing aid only.
    void gmMovieSkipPlayback(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        (void)rdram;
        (void)runtime;
        SET_GPR_U32(ctx, 2, 1u);
        ctx->pc = GPR_U32(ctx, 31);
    }

    // PS2X_WIDESCREEN=1: the community 16:9 patch (PCSX2's SLUS-20294 pnach), applied at run time
    // instead of to the code so the original stays the default:
    //  - horizontal FOV: the first lane of AiCube's projection scale vector at 0x26FC70 becomes
    //    0.75 (PCSX2 rewrites it every frame; here it is written right before
    //    AirNgp_LoadIdentity @ 0x1D0420 copies the vector into the render context);
    //  - 2D layer: the FOV scale also squeezes 2D drawing into the left 3/4 of the screen, so the
    //    pnach turns the 640.0 passed as the 2D projection width (renderer vtable +0x64 =
    //    0x1CF810, eight call sites) into 480.0, stretching 2D back to full width like PCSX2;
    //    here the renderer method itself is wrapped. PS2X_WIDESCREEN_HUD=0 leaves it squeezed.
    namespace widescreen
    {
        constexpr uint32_t kProjectionScale = 0x0026FC70u;
        constexpr uint32_t kRendererPtr = 0x00264684u;
        PS2Runtime::RecompiledFunction g_setProjectionScale = nullptr;
        PS2Runtime::RecompiledFunction g_setOrtho2d = nullptr;
        bool g_hud = true;

        uint32_t read32(const uint8_t *rdram, uint32_t address)
        {
            uint32_t value = 0u;
            std::memcpy(&value, rdram + (address & 0x01FFFFFFu), sizeof(value));
            return value;
        }

        void setOrtho2d(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
        {
            if (ctx->f[13] == 640.0f)
                ctx->f[13] = 480.0f;
            g_setOrtho2d(rdram, ctx, runtime);
        }

        void setProjectionScale(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
        {
            const float fov = 0.75f;
            std::memcpy(rdram + kProjectionScale, &fov, sizeof(fov));
            if (g_hud && !g_setOrtho2d)
            {
                const uint32_t renderer = read32(rdram, kRendererPtr);
                const uint32_t method = renderer ? read32(rdram, read32(rdram, renderer) + 0x64u) : 0u;
                if (method && runtime->hasFunction(method))
                {
                    g_setOrtho2d = runtime->lookupFunction(method);
                    runtime->replaceFunction(method, &setOrtho2d);
                    std::fprintf(stderr, "[widescreen] HUD projection hook on renderer method 0x%x\n", method);
                }
            }
            g_setProjectionScale(rdram, ctx, runtime);
        }
    }

    void applyGitarooOverrides(PS2Runtime &runtime)
    {
        if (const char *ws = std::getenv("PS2X_WIDESCREEN"); ws && std::strcmp(ws, "0") != 0)
        {
            const char *hud = std::getenv("PS2X_WIDESCREEN_HUD");
            widescreen::g_hud = !(hud && std::strcmp(hud, "0") == 0);
            widescreen::g_setProjectionScale = runtime.lookupFunction(0x001D0420u);
            runtime.replaceFunction(0x001D0420u, &widescreen::setProjectionScale);
            runtime.setDisplayAspect(16.0f / 9.0f);
        }
        runtime.replaceFunction(0x00242640u, &gmMovieSendEndCode);
        if (std::getenv("PS2X_SKIP_MOVIES") != nullptr)
        {
            runtime.replaceFunction(0x00240530u, &gmMovieSkipPlayback);
        }
    }
}

PS2_REGISTER_GAME_OVERRIDE("Gitaroo Man (USA)", "SLUS_202.94", kGitarooEntry, 0u, applyGitarooOverrides);
