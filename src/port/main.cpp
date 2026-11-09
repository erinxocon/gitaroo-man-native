// Entry point of the Gitaroo Man port. Host-side set-up that native code needs goes here, before
// the runtime starts the recompiled game.

#include "sound/sound_engine.h"

#include "ps2_runtime.h"
#include "ps2x/iop/iop_audio.h"

#include <cstdio>
#include <cstdlib>
#include <exception>
#include <filesystem>
#include <string>

namespace
{
    // The game's vblank interrupt handler (AIR.NGP driver), entered through the function table.
    constexpr uint32_t kVBlankHandler = 0x001C8AD0u;
    PS2Runtime::RecompiledFunction g_vblankHandler = nullptr;

    void vblankHandler(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        port::sound::latchSongClock();
        g_vblankHandler(rdram, ctx, runtime);
    }
}

int main(int argc, char *argv[])
{
    try
    {
        const std::filesystem::path elf = argc >= 2 ? argv[1] : "iso/SLUS_202.94";

        PS2Runtime runtime;
        if (!runtime.initialize("Gitaroo Man"))
        {
            std::fprintf(stderr, "Failed to initialize the runtime\n");
            return 1;
        }
        if (!runtime.loadELF(elf.string()))
        {
            std::fprintf(stderr, "Failed to load %s\n", elf.string().c_str());
            return 1;
        }
        // Sound is native: the emulated sound chip, stepped while other IOP modules run, is unheard.
        ps2x::iop::setAudioSink(nullptr, nullptr);
        port::sound::start();
        g_vblankHandler = runtime.lookupFunction(kVBlankHandler);
        runtime.replaceFunction(kVBlankHandler, &vblankHandler);

        runtime.run();
        std::fflush(nullptr);
        std::_Exit(0);
    }
    catch (const std::exception &e)
    {
        std::fprintf(stderr, "fatal: %s\n", e.what());
    }
    std::fflush(nullptr);
    std::_Exit(1);
}
