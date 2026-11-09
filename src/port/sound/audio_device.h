#pragma once

#include <cstdint>

// The sound card: 48 kHz stereo, pulled from a callback on the device's thread.
namespace port::sound::device
{
    using Render = void (*)(void *user, int16_t *out, uint32_t frames);

    bool open(Render render, void *user);
    // Frames between a pull and the speaker (the device's buffer).
    uint32_t bufferedFrames();
}
