// Gitaroo Man (SLUS-202.94) specific runtime overrides.
//
// Each override replaces one guest function by address. Keep them minimal and document why the
// original code cannot run as-is under the recompiled runtime.

#include "game_overrides.h"
#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"

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

    void applyGitarooOverrides(PS2Runtime &runtime)
    {
        runtime.replaceFunction(0x00242640u, &gmMovieSendEndCode);
        if (std::getenv("PS2X_SKIP_MOVIES") != nullptr)
        {
            runtime.replaceFunction(0x00240530u, &gmMovieSkipPlayback);
        }
    }
}

PS2_REGISTER_GAME_OVERRIDE("Gitaroo Man (USA)", "SLUS_202.94", kGitarooEntry, 0u, applyGitarooOverrides);
