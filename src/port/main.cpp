// Entry point of the Gitaroo Man port. Host-side set-up that native code needs goes here, before
// the runtime starts the recompiled game.

#include "ps2_runtime.h"

#include <cstdio>
#include <cstdlib>
#include <exception>
#include <filesystem>
#include <string>

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
