#include "audio_device.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>

// raylib builds and exports miniaudio with these options; they change the layout of ma_device, so
// they must match its build.
#define MA_NO_JACK
#define MA_NO_WAV
#define MA_NO_FLAC
#define MA_NO_MP3
#define MA_NO_RESOURCE_MANAGER
#define MA_NO_NODE_GRAPH
#define MA_NO_ENGINE
#define MA_NO_GENERATION
#include "external/miniaudio.h"

namespace port::sound::device
{
    namespace
    {
        constexpr uint32_t kSampleRate = 48000u;

        ma_device g_device{};
        Render g_render = nullptr;
        void *g_user = nullptr;
        uint32_t g_bufferedFrames = 0;

        // GITAROO_AUDIO_DUMP=<file.wav> records exactly what the device is given.
        std::FILE *g_dump = nullptr;
        uint32_t g_dumpFrames = 0;
        uint32_t g_dumpHeaderFrames = 0;

        void writeWavHeader()
        {
            const uint32_t dataBytes = g_dumpFrames * 4u;
            uint8_t header[44]{};
            const auto put32 = [&](size_t at, uint32_t v) { std::memcpy(header + at, &v, 4); };
            const auto put16 = [&](size_t at, uint16_t v) { std::memcpy(header + at, &v, 2); };
            std::memcpy(header, "RIFF", 4);
            put32(4, 36u + dataBytes);
            std::memcpy(header + 8, "WAVEfmt ", 8);
            put32(16, 16u);
            put16(20, 1u);
            put16(22, 2u);
            put32(24, kSampleRate);
            put32(28, kSampleRate * 4u);
            put16(32, 4u);
            put16(34, 16u);
            std::memcpy(header + 36, "data", 4);
            put32(40, dataBytes);
            const long position = std::ftell(g_dump);
            std::fseek(g_dump, 0, SEEK_SET);
            std::fwrite(header, 1, sizeof(header), g_dump);
            if (position > static_cast<long>(sizeof(header)))
                std::fseek(g_dump, position, SEEK_SET);
            std::fflush(g_dump);
        }

        void callback(ma_device *, void *output, const void *, ma_uint32 frames)
        {
            g_render(g_user, static_cast<int16_t *>(output), frames);
            if (!g_dump)
                return;
            std::fwrite(output, 4u, frames, g_dump);
            g_dumpFrames += frames;
            if (g_dumpFrames - g_dumpHeaderFrames >= kSampleRate / 2u)
            {
                writeWavHeader();
                g_dumpHeaderFrames = g_dumpFrames;
            }
        }
    }

    bool open(Render render, void *user)
    {
        g_render = render;
        g_user = user;
        if (const char *path = std::getenv("GITAROO_AUDIO_DUMP"); path && *path)
        {
            g_dump = std::fopen(path, "wb");
            if (g_dump)
                writeWavHeader();
        }
        const char *periodEnv = std::getenv("PS2X_AUDIO_PERIOD_MS");
        const long periodMs = periodEnv ? std::clamp(std::strtol(periodEnv, nullptr, 10), 1L, 100L) : 5L;
        ma_device_config config = ma_device_config_init(ma_device_type_playback);
        config.playback.format = ma_format_s16;
        config.playback.channels = 2;
        config.sampleRate = kSampleRate;
        config.periodSizeInMilliseconds = static_cast<ma_uint32>(periodMs);
        config.periods = 3;
        config.performanceProfile = ma_performance_profile_low_latency;
        config.dataCallback = callback;
        if (ma_device_init(nullptr, &config, &g_device) != MA_SUCCESS)
            return false;
        g_bufferedFrames = static_cast<uint32_t>(static_cast<uint64_t>(g_device.playback.internalPeriodSizeInFrames) *
                                                 g_device.playback.internalPeriods * kSampleRate /
                                                 g_device.playback.internalSampleRate);
        if (ma_device_start(&g_device) != MA_SUCCESS)
        {
            ma_device_uninit(&g_device);
            return false;
        }
        std::printf("[sound] %s device, %u frames buffered\n", ma_get_backend_name(g_device.pContext->backend), g_bufferedFrames);
        return true;
    }

    uint32_t bufferedFrames()
    {
        return g_bufferedFrames;
    }
}
