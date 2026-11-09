// The game's sound, native (docs/native-audio.md).
//
// The game asks for sound through iNiS's MSTREAM driver on the PS2's IOP. Here those requests are
// served directly (src/port/sound/game_functions.cpp replaces the game's request function): each
// stream channel is a host voice reading PS-ADPCM from the IMC libraries, and the sound card pulls
// the mix. What the game can observe is kept as MSTREAM has it: the reply words (the reply buffer is
// never cleared, so stale words stay), stream slot and voice allocation (a voice keeps its stored
// volume and pan across streams), the status word, which MSTREAM changes on its refill schedule
// rather than when the sound is heard, and the song clock in 28-sample blocks. Addresses in
// comments are MSTREAM's (base 0x4AA00).
#include "sound_engine.h"

#include "audio_device.h"
#include "ps2_runtime.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <cctype>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <filesystem>
#include <fstream>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace port::sound
{
    namespace
    {
        constexpr int kSlots = 256;
        constexpr int kVoices = 24;
        constexpr int kQueueEntries = 256;
        constexpr int kLibraryEntries = 128;

        bool envOn(const char *name)
        {
            const char *value = std::getenv(name);
            return value && *value && std::strcmp(value, "0") != 0;
        }

        bool traceOn()
        {
            static const bool on = envOn("GITAROO_TRACE_SOUND");
            return on;
        }

#define NATIVE_AUDIO_TRACE(...)                        \
    do                                                 \
    {                                                  \
        if (traceOn())                                 \
        {                                              \
            std::fprintf(stderr, "[sound] " __VA_ARGS__); \
        }                                              \
    } while (0)

        int32_t clamp16(int32_t value)
        {
            return std::clamp(value, -32768, 32767);
        }

        // MSTREAM's volumes are 15-bit: 0x3FFF is full scale.
        float volumeGain(uint16_t volume)
        {
            return static_cast<float>(static_cast<int16_t>(static_cast<uint16_t>(volume << 1u))) / 32768.0f;
        }

        int32_t catmullRom(const std::array<int32_t, 4> &w, uint32_t frac)
        {
            const int64_t t = frac;
            const int64_t a = -w[0] + 3 * w[1] - 3 * w[2] + w[3];
            const int64_t b = 2 * w[0] - 5 * w[1] + 4 * w[2] - w[3];
            const int64_t c = -w[0] + w[2];
            int64_t v = (a * t) >> 12;
            v = ((v + b) * t) >> 12;
            v = ((v + c) * t) >> 12;
            return clamp16(static_cast<int32_t>(w[1] + v / 2));
        }

        // The SPU pitch for a stream's sample rate (0x4EB40); 0 (silent) for any other rate.
        uint16_t pitchForRate(int32_t rate)
        {
            switch (rate)
            {
            case 11025: return 0x3AD;
            case 16000: return 0x555;
            case 22050: return 0x75A;
            case 32000: return 0xAAB;
            case 44100: return 0xEB3;
            case 48000: return 0x1000;
            default: return 0;
            }
        }

        uint32_t readU32(const uint8_t *p)
        {
            return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) |
                   (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24);
        }

        // PS-ADPCM data of one sound as its voices read it: channel c's block b is at 16-byte
        // index ((b / span) * channels + c) * span + b % span, which is the IMC layout of a stream
        // (span = interleave) and channel-major for sounds loaded whole (span = all its blocks).
        struct SoundData
        {
            uint32_t channels = 1;
            uint32_t span = 1;
            uint32_t blocksPerChannel = 0;
            std::vector<uint8_t> bytes;
            std::atomic<bool> ready{false};
            std::atomic<bool> failed{false};

            const uint8_t *block(uint32_t channel, uint32_t index) const
            {
                const size_t at = (static_cast<size_t>(index / span) * channels + channel) * span + index % span;
                return at * 16u + 16u <= bytes.size() ? bytes.data() + at * 16u : nullptr;
            }
        };

        // Reads stream data off the disc ahead of play, so neither the game thread nor the audio
        // thread waits on file reads.
        class Loader
        {
        public:
            struct Job
            {
                std::shared_ptr<SoundData> data;
                std::string hostPath;
                uint64_t offset = 0;
                uint64_t size = 0;
            };

            Loader()
                : m_thread([this] { run(); })
            {
                m_thread.detach();
            }

            void enqueue(Job job)
            {
                {
                    std::lock_guard<std::mutex> lock(m_mutex);
                    m_jobs.push_back(std::move(job));
                }
                m_wake.notify_one();
            }

            void wait(const std::shared_ptr<SoundData> &data)
            {
                std::unique_lock<std::mutex> lock(m_mutex);
                m_done.wait(lock, [&] { return data->ready.load() || data->failed.load(); });
            }

            static bool readRange(const std::string &path, uint64_t offset, uint64_t size, std::vector<uint8_t> &out)
            {
                std::ifstream file(path, std::ios::binary);
                if (!file)
                    return false;
                file.seekg(static_cast<std::streamoff>(offset));
                out.resize(static_cast<size_t>(size));
                file.read(reinterpret_cast<char *>(out.data()), static_cast<std::streamsize>(size));
                out.resize(static_cast<size_t>(file.gcount()));
                return file.gcount() > 0;
            }

        private:
            void run()
            {
                for (;;)
                {
                    Job job;
                    {
                        std::unique_lock<std::mutex> lock(m_mutex);
                        m_wake.wait(lock, [&] { return !m_jobs.empty(); });
                        job = std::move(m_jobs.front());
                        m_jobs.pop_front();
                    }
                    // Data nobody holds any more (closed before it loaded) is skipped.
                    if (job.data.use_count() > 1 && readRange(job.hostPath, job.offset, job.size, job.data->bytes))
                        job.data->ready.store(true, std::memory_order_release);
                    else
                        job.data->failed.store(true);
                    {
                        std::lock_guard<std::mutex> lock(m_mutex);
                    }
                    m_done.notify_all();
                }
            }

            std::mutex m_mutex;
            std::condition_variable m_wake;
            std::condition_variable m_done;
            std::deque<Job> m_jobs;
            std::thread m_thread;
        };

        struct Voice
        {
            // Left and right volume from MSTREAM's pan law, and the rate.
            uint16_t volL = 0;
            uint16_t volR = 0;
            uint16_t pitch = 0;

            // Keyed on and not off: the voice's timeline runs (the refill schedule and the song
            // clock follow it), whether or not it still has data to play.
            bool keyed = false;
            bool sounding = false;
            std::shared_ptr<const SoundData> data;
            uint32_t channel = 0;
            uint32_t block = 0;
            uint32_t loopBlock = 0;
            bool endAfterBlock = false;
            int16_t decoded[28]{};
            uint32_t decodedPos = 28;
            int32_t hist1 = 0;
            int32_t hist2 = 0;
            std::array<int32_t, 4> window{};
            uint32_t phase = 0;
            uint64_t samples = 0; // consumed since key-on

            // A seamless section change: from timeline block `switchAt` on, play `next` from its start.
            std::shared_ptr<const SoundData> next;
            uint32_t nextChannel = 0;
            uint64_t switchAt = UINT64_MAX;
        };

        // MSTREAM's per-voice record (0x54B20 + core * 0x120 + voice * 12).
        struct VoiceRecord
        {
            bool used = false;
            uint16_t volume = 0;
            uint16_t pan = 0;
            int32_t mute = 0;
        };

        // MSTREAM's 0xF8-byte stream record (0x55FB0 + id * 0xF8), the fields that matter.
        struct Slot
        {
            int32_t kind = 0; // +0x00: 0 free, 1 library stream, 3 loaded whole, 4 from EE memory
            int32_t channels = 0, rate = 0, interleave = 0, length = 0; // +0x04..+0x10, the IMC header
            int32_t shareBase = 0;   // +0x14: -1, or the stream whose voices this one plays on
            int32_t fromLibrary = 0; // +0x18
            int32_t entry = 0;       // +0x1C
            int32_t voices[8]{};     // +0x20
            int32_t core = 0;        // +0x60
            uint16_t pitch = 0;      // +0x64
            int32_t rows = 0;        // +0x68: length / channels, chunks of `interleave` blocks per channel
            int32_t consumed = 0;    // +0x7C: blocks of the IOP buffer uploaded, all channels
            int32_t status = 0;      // +0x84
            int32_t preloading = 0;  // +0xEC
            std::shared_ptr<SoundData> data;
        };

        // A stream being refilled (MSTREAM's per-core refill list at 0x522C0, one entry per channel;
        // all channels of a stream move together, so one record per stream). Rounds fall on the
        // current stream's chunk boundaries: MSTREAM points the SPU IRQ at the start of the next
        // chunk, and after a section change at the new section's chunks, which the split shifted.
        struct Run
        {
            int32_t slot = -1;
            uint32_t span = 0;        // SPU buffer size in blocks (+0x74, the interleave)
            uint64_t streamStart = 0; // timeline block where the current stream began
            uint32_t nextRound = 1;   // the next round is at streamStart + nextRound * span
            int32_t counter = 0;      // entry +0x18
            int32_t limit = 0;        // entry +0x14
        };

        struct LibraryEntry
        {
            char name[9]{};
            uint32_t offset = 0;
            uint32_t kind = 0;
        };

        // The movie's PCM on its way to the mixer: blocks of 256 left then 256 right samples.
        struct Movie
        {
            bool playing = false;
            std::vector<uint8_t> ring;
            size_t read = 0, write = 0, queued = 0;
            uint32_t frame = 0; // within the block at `read`
        };

        class Engine
        {
        public:
            void start()
            {
                {
                    std::lock_guard<std::mutex> lock(m_mutex);
                    if (m_started)
                        return;
                    m_started = true;
                }
                if (device::open(&Engine::pull, this))
                    m_clockOffsetFrames = device::bufferedFrames();
                else
                {
                    // No sound card: keep the song clock running, the game depends on it.
                    std::fprintf(stderr, "[sound] no audio device; mixing silently in real time\n");
                    std::thread([this] { silentMix(); }).detach();
                }
                if (const char *offset = std::getenv("GITAROO_SONG_CLOCK_OFFSET_MS"))
                    m_clockOffsetFrames = static_cast<int64_t>(std::strtod(offset, nullptr) * 48.0);
                std::printf("[sound] native sound, song clock %lld frames behind the mix\n", static_cast<long long>(m_clockOffsetFrames));
            }

            void request(PS2Runtime &runtime, uint32_t fno, const uint8_t *send, uint32_t sendSize, uint8_t *reply)
            {
                std::array<uint32_t, 16> args{};
                std::array<uint8_t, 64> raw{};
                if (send)
                {
                    std::memcpy(args.data(), send, std::min<size_t>(sendSize, sizeof(args)));
                    std::memcpy(raw.data(), send, std::min<size_t>(sendSize, raw.size()));
                }
                std::lock_guard<std::mutex> lock(m_mutex);
                mstream(runtime, fno, args, raw);
                std::memcpy(reply, m_reply.data(), 16u);
            }

            // MSTREAM's vblank handler (0x4DA4C -> 0x4D830): the clock voice's position, in blocks of
            // 28 samples, never going back. The game reads the value of the last vblank.
            void latchSongClock()
            {
                std::lock_guard<std::mutex> lock(m_mutex);
                if (!m_clockRunning || !m_threads)
                    return;
                const Voice &v = voice(m_clockCore, m_clockVoice);
                const int64_t behind = m_clockOffsetFrames * v.pitch / 0x1000;
                const int64_t samples = static_cast<int64_t>(v.samples) - behind;
                if (samples > 0)
                    m_clock = std::max<uint32_t>(m_clock, static_cast<uint32_t>(samples / 28) * 28u);
            }

            void setMaster(uint16_t volume)
            {
                std::lock_guard<std::mutex> lock(m_mutex);
                m_master = volumeGain(volume);
            }

            void movieOpen(uint32_t bytes)
            {
                std::lock_guard<std::mutex> lock(m_mutex);
                m_movie = Movie{};
                m_movie.ring.assign(bytes & ~0x3FFu, 0u);
            }

            void moviePlay(bool playing)
            {
                std::lock_guard<std::mutex> lock(m_mutex);
                m_movie.playing = playing;
            }

            void movieClear()
            {
                std::lock_guard<std::mutex> lock(m_mutex);
                m_movie.read = m_movie.write = m_movie.queued = 0;
                m_movie.frame = 0;
            }

            uint32_t movieFree()
            {
                std::lock_guard<std::mutex> lock(m_mutex);
                return static_cast<uint32_t>(m_movie.ring.size() - m_movie.queued);
            }

            void moviePut(const uint8_t *data, uint32_t bytes)
            {
                std::lock_guard<std::mutex> lock(m_mutex);
                if (m_movie.ring.empty())
                    return;
                bytes = std::min<uint32_t>(bytes, static_cast<uint32_t>(m_movie.ring.size() - m_movie.queued));
                for (uint32_t i = 0; i < bytes; ++i)
                {
                    m_movie.ring[m_movie.write] = data[i];
                    m_movie.write = (m_movie.write + 1u) % m_movie.ring.size();
                }
                m_movie.queued += bytes;
            }

        private:
            static void pull(void *user, int16_t *out, uint32_t frames)
            {
                static_cast<Engine *>(user)->render(out, frames);
            }

            void silentMix()
            {
                constexpr uint32_t kFrames = 240u;
                std::array<int16_t, kFrames * 2u> scratch{};
                auto next = std::chrono::steady_clock::now();
                for (;;)
                {
                    render(scratch.data(), kFrames);
                    next += std::chrono::microseconds(5000);
                    std::this_thread::sleep_until(next);
                }
            }

            // ---------------------------------------------------------------------------------
            // Voices
            // ---------------------------------------------------------------------------------

            Voice &voice(int32_t core, int32_t index)
            {
                return m_voices[core & 1][static_cast<uint32_t>(index) % kVoices];
            }

            void keyOn(int32_t core, uint32_t mask)
            {
                for (int32_t i = 0; i < kVoices; ++i)
                {
                    if ((mask & (1u << i)) == 0u)
                        continue;
                    Voice &v = voice(core, i);
                    v.keyed = true;
                    v.sounding = v.data != nullptr;
                    v.block = 0;
                    v.loopBlock = 0;
                    v.endAfterBlock = false;
                    v.decodedPos = 28;
                    v.hist1 = v.hist2 = 0;
                    v.window = {};
                    v.phase = 0;
                    v.samples = 0;
                    v.next.reset();
                    v.switchAt = UINT64_MAX;
                }
            }

            void keyOff(int32_t core, uint32_t mask)
            {
                for (int32_t i = 0; i < kVoices; ++i)
                {
                    if ((mask & (1u << i)) == 0u)
                        continue;
                    Voice &v = voice(core, i);
                    v.keyed = false;
                    v.sounding = false;
                    v.next.reset();
                    v.switchAt = UINT64_MAX;
                }
            }

            void fetchBlock(Voice &v)
            {
                v.decodedPos = 0;
                if (v.endAfterBlock)
                {
                    v.sounding = false;
                    v.endAfterBlock = false;
                }
                if (v.next && v.samples / 28u == v.switchAt)
                {
                    v.data = std::move(v.next);
                    v.channel = v.nextChannel;
                    v.next.reset();
                    v.switchAt = UINT64_MAX;
                    v.block = 0;
                    v.loopBlock = 0;
                    v.sounding = true;
                }
                const SoundData *data = v.data.get();
                const uint8_t *b = nullptr;
                if (v.sounding && data)
                {
                    if (!data->ready.load(std::memory_order_acquire))
                    {
                        if (!m_warnedUnloaded)
                            NATIVE_AUDIO_TRACE("voice reached data still loading\n");
                        m_warnedUnloaded = true;
                        ++v.block;
                    }
                    else if (v.block >= data->blocksPerChannel || !(b = data->block(v.channel, v.block)))
                    {
                        v.sounding = false;
                    }
                }
                if (!b)
                {
                    std::fill(std::begin(v.decoded), std::end(v.decoded), int16_t{0});
                    return;
                }

                static constexpr int32_t kPos[5] = {0, 60, 115, 98, 122};
                static constexpr int32_t kNeg[5] = {0, 0, -52, -55, -60};
                uint32_t shift = b[0] & 0x0Fu;
                const uint32_t filter = std::min<uint32_t>((b[0] >> 4) & 7u, 4u);
                const uint8_t flags = b[1];
                if (shift > 12u)
                    shift = 9u;
                for (uint32_t i = 0; i < 28u; ++i)
                {
                    const int32_t nibble = (b[2 + i / 2] >> ((i & 1u) * 4u)) & 0xF;
                    int32_t sample = static_cast<int16_t>(static_cast<uint16_t>(nibble << 12)) >> shift;
                    sample += (v.hist1 * kPos[filter] + v.hist2 * kNeg[filter] + 32) >> 6;
                    sample = clamp16(sample);
                    v.hist2 = v.hist1;
                    v.hist1 = sample;
                    v.decoded[i] = static_cast<int16_t>(sample);
                }
                // Loop flags, as the data and MSTREAM leave them: 4 marks the loop start; 1 ends the
                // block, then jumps back with 2 (repeat) or stops the voice without.
                if (flags & 4u)
                    v.loopBlock = v.block;
                if (flags & 1u)
                {
                    if (flags & 2u)
                        v.block = v.loopBlock;
                    else
                    {
                        v.endAfterBlock = true;
                        ++v.block;
                    }
                }
                else
                {
                    ++v.block;
                }
            }

            int32_t voiceSample(Voice &v)
            {
                const int32_t out = catmullRom(v.window, v.phase & 0xFFFu);
                v.phase += std::min<uint32_t>(v.pitch, 0x3FFFu);
                while (v.phase >= 0x1000u)
                {
                    v.phase -= 0x1000u;
                    if (v.decodedPos >= 28u)
                        fetchBlock(v);
                    v.window[0] = v.window[1];
                    v.window[1] = v.window[2];
                    v.window[2] = v.window[3];
                    v.window[3] = v.decoded[v.decodedPos++];
                    ++v.samples;
                }
                return out;
            }

            // ---------------------------------------------------------------------------------
            // Mixer
            // ---------------------------------------------------------------------------------

            // Streams and movies pass through the master volume twice, effects once: on the PS2 they
            // reach the output through its two sound cores in turn.
            void render(int16_t *out, uint32_t frames)
            {
                std::lock_guard<std::mutex> lock(m_mutex);
                for (uint32_t f = 0; f < frames; ++f)
                {
                    float music[2] = {}, effects[2] = {};
                    for (int32_t c = 0; c < 2; ++c)
                    {
                        float *bus = c == 0 ? music : effects;
                        for (Voice &v : m_voices[c])
                        {
                            if (!v.keyed)
                                continue;
                            const float s = static_cast<float>(voiceSample(v));
                            bus[0] += s * volumeGain(v.volL);
                            bus[1] += s * volumeGain(v.volR);
                        }
                    }
                    if (m_movie.playing && m_movie.queued >= 0x400u)
                    {
                        const size_t at = m_movie.read + m_movie.frame * 2u;
                        music[0] += static_cast<float>(static_cast<int16_t>(m_movie.ring[at] | (m_movie.ring[at + 1u] << 8)));
                        music[1] += static_cast<float>(static_cast<int16_t>(m_movie.ring[at + 0x200u] | (m_movie.ring[at + 0x201u] << 8)));
                        if (++m_movie.frame == 256u)
                        {
                            m_movie.frame = 0;
                            m_movie.read = (m_movie.read + 0x400u) % m_movie.ring.size();
                            m_movie.queued -= 0x400u;
                        }
                    }
                    const float twice = m_master * m_master;
                    for (int ch = 0; ch < 2; ++ch)
                        out[f * 2u + static_cast<uint32_t>(ch)] = static_cast<int16_t>(
                            clamp16(static_cast<int32_t>(std::lrint(music[ch] * twice + effects[ch] * m_master))));

                    if (m_threads)
                        refillRounds();
                }
            }

            // ---------------------------------------------------------------------------------
            // MSTREAM's refill schedule (the refill threads 0x4D020 and FUN_0004cc4c), kept for
            // what it makes visible: when a stream's status changes. On the PS2 a refill round runs
            // each time the lead voice enters the next SPU buffer (its IRQ); the round uploads the
            // buffer after it, so a section change and a stream's end show in the status a buffer
            // or two before they are heard.
            // ---------------------------------------------------------------------------------

            void refillRounds()
            {
                for (size_t i = 0; i < m_runs.size();)
                {
                    Run &run = m_runs[i];
                    Slot &s = m_slots[run.slot];
                    const Voice &lead = voice(s.core, s.voices[0]);
                    if (!lead.keyed || run.span == 0u ||
                        lead.samples / 28u < run.streamStart + static_cast<uint64_t>(run.nextRound) * run.span)
                    {
                        ++i;
                        continue;
                    }
                    if (!refillRound(run))
                        m_runs.erase(m_runs.begin() + static_cast<std::ptrdiff_t>(i));
                    else
                        ++i;
                }
            }

            // One round: the uploads (FUN_0004cc4c per channel), which may switch to the queued
            // section, then the end check. Returns false once the stream has left the refill list.
            bool refillRound(Run &run)
            {
                const uint32_t span = run.span;
                // Blocks of the current stream in the voice's buffers before this round's upload.
                const uint32_t uploaded = (run.nextRound + 1u) * span;
                const uint32_t pos = m_eventPos;
                ++run.nextRound;
                if (pos != 0xFFFFFFFFu && m_eventSlot >= 0 && pos - 2u * span <= uploaded && uploaded >= pos - span)
                {
                    Slot &from = m_slots[run.slot];
                    Slot &to = m_slots[m_eventSlot];
                    const uint64_t switchAt = run.streamStart + pos;
                    for (int32_t c = 0; c < from.channels && c < 8; ++c)
                    {
                        Voice &v = voice(from.core, from.voices[c]);
                        v.next = to.data;
                        v.nextChannel = static_cast<uint32_t>(c);
                        v.switchAt = switchAt;
                    }
                    NATIVE_AUDIO_TRACE("section change: stream %d -> %d at block %u (timeline %llu), status flips now\n", run.slot,
                                       m_eventSlot, pos, static_cast<unsigned long long>(switchAt));
                    from.status = 0;
                    to.status = 1;
                    run.slot = m_eventSlot;
                    run.streamStart = switchAt;
                    run.nextRound = 0;
                    run.counter = 0;
                    run.limit = to.rows;
                    m_eventPos = 0xFFFFFFFFu;
                }
                if (run.counter < run.limit - 1)
                {
                    ++run.counter;
                    return true;
                }
                NATIVE_AUDIO_TRACE("stream %d leaves the refill list, status 0\n", run.slot);
                m_slots[run.slot].status = 0;
                return false;
            }

            void removeRun(int32_t slot)
            {
                m_runs.erase(std::remove_if(m_runs.begin(), m_runs.end(), [&](const Run &r) { return r.slot == slot; }),
                             m_runs.end());
            }

            // ---------------------------------------------------------------------------------
            // MSTREAM (dispatch 0x4EE6C)
            // ---------------------------------------------------------------------------------

            // Voice volume from MSTREAM's volume and pan (0x4E6D8).
            void applyVolume(int32_t core, int32_t index, uint32_t volume, uint32_t pan)
            {
                volume &= 0xFFFFu;
                pan &= 0xFFFFu;
                int32_t left = 0, right = 0;
                const int32_t vol = static_cast<int32_t>(volume);
                if (vol == 0)
                {
                }
                else if (!m_stereo)
                {
                    int32_t d = static_cast<int32_t>(pan) - 0x3FFF;
                    if (d < 0)
                        d = -d;
                    left = ((vol - (((d * 100) / 0x3FFF) * 0x3333) / 100) >> 1) & 0xFFFF;
                    right = left;
                }
                else
                {
                    left = vol >> 1;
                    if (pan < 0x4000u)
                    {
                        const int32_t twice = static_cast<int32_t>(pan & 0x7FFFu) * 2;
                        right = twice < 0x7FFF - vol ? 0 : ((twice - (0x7FFF - vol)) >> 1) & 0xFFFF;
                    }
                    else
                    {
                        const int32_t far = static_cast<int32_t>((0x7FFFu - pan) & 0xFFFFu);
                        right = left;
                        left = far < 0x7FFF - vol ? 0 : ((far - (0x7FFF - vol)) >> 1) & 0xFFFF;
                    }
                }
                Voice &v = voice(core, index);
                v.volL = static_cast<uint16_t>(left);
                v.volR = static_cast<uint16_t>(right);
            }

            VoiceRecord &record(int32_t core, int32_t index)
            {
                return m_records[core & 1][static_cast<uint32_t>(index) % kVoices];
            }

            void setVolume(int32_t core, int32_t index, uint16_t volume, bool store) // 0x4E8A0
            {
                if (store)
                    record(core, index).volume = volume;
                applyVolume(core, index, volume, record(core, index).pan);
            }

            void setPan(int32_t core, int32_t index, uint16_t pan, bool store) // 0x4E90C
            {
                if (store)
                    record(core, index).pan = pan;
                applyVolume(core, index, record(core, index).volume, pan);
            }

            void setMute(int32_t core, int32_t index, int32_t mute) // 0x4E574
            {
                record(core, index).mute = mute;
                setVolume(core, index, mute == 0 ? record(core, index).volume : 0, false);
            }

            void setMasterVolume(uint16_t volume) // 0x4E97C
            {
                m_masterVolume = volume;
                m_master = volumeGain(static_cast<uint16_t>(volume >> 1));
            }

            // A voice for a stream channel (0x4E3D0): the first free one on the core, silent until
            // the game sets a volume.
            int32_t allocVoice(int32_t core, uint16_t pitch)
            {
                for (int32_t i = 0; i < kVoices; ++i)
                {
                    VoiceRecord &r = record(core, i);
                    if (r.used)
                        continue;
                    r.used = true;
                    Voice &v = voice(core, i);
                    v.pitch = pitch;
                    applyVolume(core, i, 0x7FFF, 0x3FFF);
                    setMute(core, i, 1);
                    return i;
                }
                return -1;
            }

            uint32_t voiceMask(const Slot &s) const // 0x4F808
            {
                uint32_t mask = 0;
                for (int32_t c = 0; c < s.channels && c < 8; ++c)
                    mask |= 1u << (static_cast<uint32_t>(s.voices[c]) & 0x1Fu);
                return mask;
            }

            int32_t freeSlot() // 0x4AD00: free means a zero length word
            {
                for (int32_t i = 0; i < kSlots; ++i)
                    if (m_slots[i].length == 0)
                        return i;
                return -1;
            }

            // FUN_0004ad64: header checks against the stream it shares voices with, derived sizes.
            bool deriveSizes(int32_t id, bool share)
            {
                Slot &s = m_slots[id];
                if (share)
                {
                    const Slot &b = m_slots[s.shareBase & 0xFF];
                    if (s.channels != b.channels || s.rate != b.rate || s.interleave != b.interleave)
                    {
                        NATIVE_AUDIO_TRACE("stream %d does not match the stream %d it shares voices with\n", id, s.shareBase);
                        closeSlot(id);
                        return false;
                    }
                }
                s.rows = s.channels ? s.length / s.channels : 0;
                s.pitch = pitchForRate(s.rate);
                return true;
            }

            bool lookup(Slot &s, const char *name)
            {
                for (int32_t i = 0; i < m_libraryCount && i < kLibraryEntries; ++i)
                {
                    if (std::strcmp(m_library[i].name, name) == 0)
                    {
                        s.fromLibrary = 1;
                        s.entry = i;
                        break;
                    }
                }
                return s.fromLibrary != 0;
            }

            bool readHeader(Slot &s)
            {
                std::vector<uint8_t> header;
                const LibraryEntry &e = m_library[s.entry % kLibraryEntries];
                if (!Loader::readRange(m_libraryPath, e.offset, 16u, header) || header.size() < 16u)
                    return false;
                s.channels = static_cast<int32_t>(readU32(header.data()));
                s.rate = static_cast<int32_t>(readU32(header.data() + 4));
                s.interleave = static_cast<int32_t>(readU32(header.data() + 8));
                s.length = static_cast<int32_t>(readU32(header.data() + 12));
                return s.channels >= 1 && s.channels <= 8;
            }

            void setOpenReply(int32_t id)
            {
                m_lastChannels = static_cast<uint32_t>(m_slots[id].channels);
                m_lastRate = static_cast<uint32_t>(m_slots[id].rate);
                m_lastId = static_cast<uint32_t>(id);
            }

            // 0x1400 (0x4B4A8): a stream from the library, read as it plays.
            uint32_t openStream(const char *name, int32_t share, int32_t base)
            {
                const int32_t id = freeSlot();
                if (id < 0)
                    return 1;
                Slot &s = m_slots[id];
                s.core = 0;
                if (share == 0)
                    s.shareBase = -1;
                else
                {
                    s.shareBase = base;
                    m_slots[base & 0xFF].shareBase = base;
                }
                if (!lookup(s, name))
                    return 1;
                if (!readHeader(s))
                {
                    std::fprintf(stderr, "[sound] stream %s: bad header\n", name);
                    return 1;
                }
                s.kind = 1;
                if (!deriveSizes(id, share != 0))
                    return 1;
                if (share == 0)
                {
                    for (int32_t c = 0; c < s.channels; ++c)
                    {
                        const int32_t v = allocVoice(s.core, s.pitch);
                        if (v < 0)
                        {
                            std::fprintf(stderr, "[sound] FATAL: no free voice for stream %s\n", name);
                            return 1;
                        }
                        s.voices[c] = v;
                    }
                }
                else
                {
                    std::copy(std::begin(m_slots[base & 0xFF].voices), std::end(m_slots[base & 0xFF].voices), s.voices);
                }
                auto data = std::make_shared<SoundData>();
                data->channels = static_cast<uint32_t>(s.channels);
                data->span = static_cast<uint32_t>(std::max(s.interleave, 1));
                data->blocksPerChannel = static_cast<uint32_t>(s.rows) * data->span;
                s.data = data;
                const uint64_t bytes = static_cast<uint64_t>(s.rows) * s.channels * data->span * 16u;
                m_loader.enqueue({data, m_libraryPath, m_library[s.entry].offset + 16ull, bytes});
                setOpenReply(id);
                NATIVE_AUDIO_TRACE("open stream %s as %d: %d ch, %d Hz, interleave 0x%x, %d rows, voices %d.. on core 0%s\n",
                                   name, id, s.channels, s.rate, s.interleave, s.rows, s.voices[0],
                                   share ? " (shared)" : "");
                return 0;
            }

            // 0x1500 (0x4C064): a sound loaded whole into its voices' memory.
            uint32_t openLocal(const char *name, int32_t loop)
            {
                const int32_t id = freeSlot();
                if (id < 0)
                    return 1;
                Slot &s = m_slots[id];
                s.core = 1;
                s.shareBase = -1;
                if (!lookup(s, name))
                {
                    std::fprintf(stderr, "[sound] could not find %s in the library\n", name);
                    return 1;
                }
                if (!readHeader(s))
                {
                    std::fprintf(stderr, "[sound] sound %s: bad header\n", name);
                    return 1;
                }
                s.kind = 3;
                if (!deriveSizes(id, false))
                    return 1;
                for (int32_t c = 0; c < s.channels; ++c)
                {
                    const int32_t v = allocVoice(s.core, s.pitch);
                    if (v < 0)
                    {
                        std::fprintf(stderr, "[sound] FATAL: no free voice for sound %s\n", name);
                        return 1;
                    }
                    s.voices[c] = v;
                }

                // Each channel's rows go to its voice's memory one after the other, with the loop
                // flags MSTREAM writes over the first and last blocks of rows.
                const uint32_t span = static_cast<uint32_t>(std::max(s.interleave, 1));
                const uint32_t rowBytes = span * 16u;
                const uint32_t rows = static_cast<uint32_t>(std::max(s.rows, 0));
                std::vector<uint8_t> file;
                const uint64_t total = static_cast<uint64_t>(rows) * s.channels * rowBytes;
                (void)Loader::readRange(m_libraryPath, m_library[s.entry].offset + 16ull, total, file);
                file.resize(static_cast<size_t>(total), 0u);
                auto data = std::make_shared<SoundData>();
                data->channels = static_cast<uint32_t>(s.channels);
                data->span = rows * span;
                data->blocksPerChannel = rows * span;
                data->bytes.resize(static_cast<size_t>(total));
                for (uint32_t r = 0; r < rows; ++r)
                {
                    for (uint32_t c = 0; c < static_cast<uint32_t>(s.channels); ++c)
                    {
                        uint8_t *row = data->bytes.data() + (static_cast<size_t>(c) * rows + r) * rowBytes;
                        std::memcpy(row, file.data() + (static_cast<size_t>(r) * s.channels + c) * rowBytes, rowBytes);
                        if (rowBytes < 0x20u)
                            continue;
                        uint8_t &first = row[1], &second = row[0x11], &last = row[rowBytes - 0xF];
                        if (rows == 1u)
                        {
                            second = 2;
                            first = 6;
                            last = loop ? 3 : 7;
                        }
                        else
                        {
                            if (r == 0u)
                            {
                                second = 2;
                                first = 6;
                                last = 2;
                            }
                            if (loop && r == rows - 1u)
                            {
                                second = 2;
                                first = 2;
                                last = 3;
                            }
                        }
                    }
                }
                data->ready.store(true);
                s.data = data;
                setOpenReply(id);
                NATIVE_AUDIO_TRACE("open sound %s as %d: %d ch, %d Hz, %u blocks, voices %d.. on core 1%s\n", name, id,
                                   s.channels, s.rate, rows * span, s.voices[0], loop ? ", looped" : "");
                return 0;
            }

            // 0x0A00 (0x4D694).
            void stopSlot(int32_t id)
            {
                Slot &s = m_slots[id & 0xFF];
                keyOff(s.core, voiceMask(s));
                if ((s.kind == 1 || s.kind == 4) && s.status != 0)
                {
                    m_clockRunning = false;
                    removeRun(id & 0xFF);
                    s.status = 0;
                }
            }

            // 0x1F00 (0x4C90C).
            void closeSlot(int32_t id)
            {
                Slot &s = m_slots[id & 0xFF];
                if (s.kind == 0)
                    return;
                if (s.shareBase == id || s.shareBase == -1)
                {
                    stopSlot(id);
                    for (int32_t c = 0; c < s.channels && c < 8; ++c)
                        record(s.core, s.voices[c]).used = false;
                }
                s.entry = 0;
                s.length = 0;
                s.kind = 0;
                s.data.reset();
            }

            // 0x0800 (0x4D4AC).
            void play(int32_t id, uint32_t streaming, uint32_t resetClock, uint32_t signal)
            {
                Slot &s = m_slots[id & 0xFF];
                if (signal)
                    ++m_eventSignals;
                if (streaming)
                {
                    m_clockRunning = true;
                    removeRun(id & 0xFF);
                    Run run;
                    run.slot = id & 0xFF;
                    run.span = static_cast<uint32_t>(std::max(s.interleave, 0));
                    run.limit = s.rows - (s.channels && s.interleave ? (s.consumed / s.channels) / s.interleave : 0);
                    m_runs.push_back(run);
                }
                if (resetClock)
                {
                    m_queueRead = m_queueWrite = 0;
                    m_clock = 0;
                }
                for (int32_t c = 0; c < s.channels && c < 8; ++c)
                {
                    Voice &v = voice(s.core, s.voices[c]);
                    v.data = s.data;
                    v.channel = static_cast<uint32_t>(c);
                }
                keyOn(s.core, voiceMask(s));
                s.status = 1;
                NATIVE_AUDIO_TRACE("play %d (kind %d)%s%s\n", id, s.kind, streaming ? " streaming" : "",
                                   resetClock ? ", clock reset" : "");
            }

            // 0x2000 and the event thread (0x4C7C4): read a stream's start and, with `upload`, put it
            // in its voices' buffers. Only MSTREAM's IOP buffer count survives of this (+0x7C, which
            // decides how many refill rounds the stream gets).
            void preload(int32_t id, uint32_t upload)
            {
                Slot &s = m_slots[id & 0xFF];
                s.consumed = 0;
                if (s.kind == 1 && s.data && upload)
                {
                    // MSTREAM reads the start before replying; here the whole stream is loading.
                    const std::shared_ptr<SoundData> data = s.data;
                    m_mutex.unlock();
                    m_loader.wait(data);
                    m_mutex.lock();
                    // Two buffers uploaded; with two IOP buffers (interleave >= 0x300) the second
                    // upload empties the first IOP buffer and the count starts again.
                    s.consumed = s.interleave >= 0x300 ? 0 : 2 * s.channels * s.interleave;
                }
            }

            // The event thread (0x4DCF4), one wake-up per signal.
            void eventThread()
            {
                if (m_queueRead < m_queueWrite)
                {
                    const QueueEntry &e = m_queue[m_queueRead % kQueueEntries];
                    m_eventSlot = static_cast<int32_t>(e.stream & 0xFF);
                    m_eventPos = e.position;
                    NATIVE_AUDIO_TRACE("next section: stream %u at block %u\n", e.stream, e.position);
                    preload(static_cast<int32_t>(e.stream), 0);
                    ++m_queueRead;
                }
                else if (m_queueRead == m_queueWrite)
                {
                    m_eventPos = 0xFFFFFFFFu;
                }
            }

            void resetClockState() // 0x4DA04
            {
                m_clock = 0;
                m_eventPos = 0xFFFFFFFFu;
                m_clockRunning = false;
            }

            void init() // 0x0100
            {
                m_movie.playing = false; // the driver's init resets the sound hardware
                setMasterVolume(m_masterVolume);
                for (int32_t core = 0; core < 2; ++core)
                    for (int32_t i = 0; i < kVoices; ++i)
                        m_records[core][i].used = false;
                // The init clears 0xC0 bytes of the volume records: core 0's first 16 voices.
                for (int32_t i = 0; i < 16; ++i)
                {
                    m_records[0][i].volume = m_records[0][i].pan = 0;
                    m_records[0][i].mute = 0;
                }
                for (Slot &s : m_slots)
                    s = Slot{};
                m_library = {};
                m_preloadBusy = 0;
                m_runs.clear();
                m_threads = true;
                resetClockState();
                m_queueRead = m_queueWrite = 0;
            }

            void sleep() // 0x0E00
            {
                m_threads = false;
                m_runs.clear();
                for (Slot &s : m_slots)
                    s = Slot{};
                m_library = {};
            }

            bool openLibrary(PS2Runtime &runtime, const std::array<uint8_t, 64> &raw) // 0x1A00 (0x4B138)
            {
                std::string path(reinterpret_cast<const char *>(raw.data()), strnlen(reinterpret_cast<const char *>(raw.data()), raw.size()));
                const PS2Runtime::IoPaths &paths = PS2Runtime::getIoPaths();
                std::filesystem::path host;
                if (!runtime.vfs().resolveHostPath(path, {paths.hostRoot, paths.cdRoot, paths.mcRoot}, host))
                {
                    std::fprintf(stderr, "[sound] cannot resolve library %s\n", path.c_str());
                    return false;
                }
                std::vector<uint8_t> head;
                if (!Loader::readRange(host.string(), 0, 4, head) || head.size() < 4)
                {
                    std::fprintf(stderr, "[sound] cannot read library %s\n", host.string().c_str());
                    return false;
                }
                m_libraryPath = host.string();
                m_libraryCount = static_cast<int32_t>(readU32(head.data()));
                std::vector<uint8_t> table;
                const uint32_t count = static_cast<uint32_t>(std::clamp(m_libraryCount, 0, kLibraryEntries));
                (void)Loader::readRange(m_libraryPath, 4, count * 32ull, table);
                table.resize(count * 32u, 0u);
                for (uint32_t i = 0; i < count; ++i)
                {
                    LibraryEntry &e = m_library[i];
                    const uint8_t *p = table.data() + i * 32u;
                    // The name is upper-cased over its first 8 bytes; byte 8 is the 8-character
                    // names' terminator.
                    for (uint32_t k = 0; k < 8; ++k)
                        e.name[k] = static_cast<char>(std::toupper(p[k]));
                    e.name[8] = '\0';
                    e.offset = readU32(p + 0x10);
                    e.kind = readU32(p + 0x1C);
                }
                NATIVE_AUDIO_TRACE("library %s: %d entries\n", path.c_str(), m_libraryCount);
                return true;
            }

            void mstream(PS2Runtime &runtime, uint32_t fno, const std::array<uint32_t, 16> &a, const std::array<uint8_t, 64> &raw)
            {
                const uint32_t cmd = fno & 0xFFFFFF00u;
                const int32_t id = static_cast<int32_t>(a[0]);
                const auto name = [&]() -> std::string
                { return std::string(reinterpret_cast<const char *>(raw.data()), strnlen(reinterpret_cast<const char *>(raw.data()), 0x3E)); };
                const auto shareFlag = static_cast<int32_t>(static_cast<int8_t>(raw[0x3E]));
                const auto shareBase = static_cast<int32_t>(static_cast<int8_t>(raw[0x3F]));
                switch (cmd)
                {
                case 0xF100:
                    break;
                case 0x5C00:
                    m_queueRead = m_queueWrite = 0;
                    m_reply[0] = cmd; // the void call leaves v0 = the command
                    break;
                case 0x5B00:
                    m_stereo = a[0] != 0;
                    break;
                case 0x5800:
                    m_queue[m_queueWrite % kQueueEntries] = {a[0], a[1], a[2]};
                    ++m_queueWrite;
                    if (a[3])
                        ++m_eventSignals;
                    break;
                case 0x5300:
                    if (id < kSlots)
                        setMute(m_slots[id].core, m_slots[id].voices[a[1] & 7], static_cast<int32_t>(a[2]));
                    break;
                case 0x5200:
                    if (id < kSlots)
                        setPan(m_slots[id].core, m_slots[id].voices[a[1] & 7], static_cast<uint16_t>(a[2]), true);
                    break;
                case 0x5100:
                    if (id < kSlots)
                        setVolume(m_slots[id].core, m_slots[id].voices[a[1] & 7], static_cast<uint16_t>(a[2]), true);
                    break;
                case 0x5A00:
                    setMasterVolume(static_cast<uint16_t>(a[0]));
                    break;
                case 0x0800:
                    if (id < kSlots)
                        play(id, a[1], a[2], a[3]);
                    break;
                case 0x5600:
                    m_reply[0] = m_clock;
                    break;
                case 0x2200:
                    m_reply[0] = m_preloadBusy;
                    break;
                case 0x2000:
                    m_slots[id & 0xFF].preloading = 1;
                    preload(id, a[2]);
                    m_slots[id & 0xFF].preloading = 0;
                    m_reply[0] = 0;
                    break;
                case 0x2100:
                    m_slots[id & 0xFF].preloading = 1;
                    m_reply[0] = 1; // queued for MSTREAM's command thread, which preloads at once here
                    preload(id, a[2]);
                    m_slots[id & 0xFF].preloading = 0;
                    m_preloadBusy = 0;
                    break;
                case 0x0900:
                    m_reply[0] = static_cast<uint32_t>(m_slots[id & 0xFF].status);
                    break;
                case 0x0A00:
                    stopSlot(id);
                    break;
                case 0x1F00:
                    closeSlot(id);
                    break;
                case 0x0D00:
                    for (int32_t i = kSlots - 1; i >= 0; --i)
                        closeSlot(i);
                    break;
                case 0x1B00:
                    for (int32_t i = kSlots - 1; i >= 0; --i)
                        closeSlot(i);
                    m_reply[0] = 0;
                    break;
                case 0x1A00:
                    m_reply[0] = openLibrary(runtime, raw) ? 0u : 1u;
                    break;
                case 0x1500:
                case 0x1400:
                {
                    const std::string n = name();
                    m_reply[0] = cmd == 0x1400 ? openStream(n.c_str(), shareFlag, shareBase) : openLocal(n.c_str(), shareBase);
                    m_reply[1] = m_lastChannels;
                    m_reply[2] = m_lastRate;
                    m_reply[3] = m_lastId;
                    break;
                }
                case 0x1000:
                case 0x1100:
                case 0x1300:
                    std::fprintf(stderr, "[sound] MSTREAM open 0x%x (by path or from EE memory) is not supported\n", cmd);
                    m_reply[0] = 1;
                    break;
                case 0x5700:
                {
                    const Slot &s = m_slots[id & 0xFF];
                    m_clockCore = s.core;
                    m_clockVoice = s.voices[0];
                    resetClockState();
                    m_reply[0] = 0;
                    break;
                }
                case 0x3200:
                    m_threads = false;
                    break;
                case 0x3100:
                    m_threads = true;
                    resetClockState();
                    break;
                case 0x0E00:
                    sleep();
                    break;
                case 0x0100:
                    init();
                    break;
                default:
                    std::fprintf(stderr, "[sound] MSTREAM command 0x%x not handled\n", cmd);
                    m_reply[0] = 1;
                    break;
                }
                // The event thread runs once the RPC thread has replied.
                while (m_eventSignals > 0)
                {
                    --m_eventSignals;
                    eventThread();
                }
            }

            struct QueueEntry
            {
                uint32_t stream = 0, position = 0, flag = 0;
            };

            std::mutex m_mutex;
            Loader m_loader;
            bool m_started = false;
            int64_t m_clockOffsetFrames = 0;
            float m_master = 0.0f;

            std::array<std::array<Voice, kVoices>, 2> m_voices{};
            Movie m_movie;

            std::array<uint32_t, 4> m_reply{};
            std::array<std::array<VoiceRecord, kVoices>, 2> m_records{};
            std::array<Slot, kSlots> m_slots{};
            std::array<LibraryEntry, kLibraryEntries> m_library{};
            int32_t m_libraryCount = 0;
            std::string m_libraryPath;
            uint32_t m_lastChannels = 0, m_lastRate = 0, m_lastId = 0;
            uint32_t m_preloadBusy = 0;
            bool m_stereo = true;
            uint16_t m_masterVolume = 0x7FFF;
            bool m_threads = false;
            std::vector<Run> m_runs;
            std::array<QueueEntry, kQueueEntries> m_queue{};
            uint32_t m_queueRead = 0, m_queueWrite = 0;
            uint32_t m_eventSignals = 0;
            int32_t m_eventSlot = -1;
            uint32_t m_eventPos = 0xFFFFFFFFu;
            int32_t m_clockCore = 0, m_clockVoice = 0;
            bool m_clockRunning = false;
            uint32_t m_clock = 0;

            bool m_warnedUnloaded = false;
        };

        Engine &engine()
        {
            // Never destroyed: the device thread may still pull from it while the process exits.
            static Engine *instance = new Engine();
            return *instance;
        }
    }

    void start() { engine().start(); }

    void request(PS2Runtime &runtime, uint32_t fno, const uint8_t *send, uint32_t sendSize, uint8_t *reply)
    {
        engine().request(runtime, fno, send, sendSize, reply);
    }

    void latchSongClock() { engine().latchSongClock(); }
    void setMaster(uint16_t volume) { engine().setMaster(volume); }
    void movieOpen(uint32_t bytes) { engine().movieOpen(bytes); }
    void moviePlay(bool playing) { engine().moviePlay(playing); }
    void movieClear() { engine().movieClear(); }
    uint32_t movieFree() { return engine().movieFree(); }
    void moviePut(const uint8_t *data, uint32_t bytes) { engine().moviePut(data, bytes); }
}
