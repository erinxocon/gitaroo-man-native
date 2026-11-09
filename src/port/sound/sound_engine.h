#pragma once

#include <cstdint>
#include <string>

// The game's sound, native (docs/native-audio.md).
namespace port::sound
{
    void start();
    // One request to the game's sound driver: `reply` receives its 16 reply bytes.
    void request(uint32_t fno, const uint8_t *send, uint32_t sendSize, uint8_t *reply);
    // Opens an IMC library, given as a path on this machine.
    bool openLibrary(const std::string &hostPath);
    // At each of the game's vblanks: the song clock the game reads until the next one.
    void latchSongClock();

    void setMovieVolume(uint16_t volume);

    // The movie player's PCM (blocks of 256 left then 256 right samples, 0x400 bytes).
    void movieOpen(uint32_t bytes);
    void moviePlay(bool playing);
    void movieClear();
    uint32_t movieFree();
    void moviePut(const uint8_t *data, uint32_t bytes);
}
