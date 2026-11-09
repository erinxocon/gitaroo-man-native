// The game's sound, native (docs/native-audio.md).
//
// The game asks for sound through one request function (src/port/sound/game_functions.cpp): open
// a library, open and play named streams, set volume and pan, queue the next song section, read the
// song clock. This engine does those things directly: each stream channel decodes its PS-ADPCM from
// the IMC library and the sound card pulls the mix. The request numbers are the game's; everything
// else is ours.
#include "sound_engine.h"

#include "audio_device.h"

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
        constexpr uint32_t kOutputRate = 48000u;
        constexpr int kMaxStreams = 256;
        constexpr int kMaxChannels = 8;
        constexpr int kLibraryEntries = 128;
        constexpr uint32_t kSamplesPerBlock = 28u;

        bool traceOn()
        {
            static const bool on = [] { const char *v = std::getenv("GITAROO_TRACE_SOUND"); return v && *v && std::strcmp(v, "0") != 0; }();
            return on;
        }

#define SOUND_TRACE(...) do { if (traceOn()) std::fprintf(stderr, "[sound] " __VA_ARGS__); } while (0)

        int32_t clamp16(int32_t value)
        {
            return std::clamp(value, -32768, 32767);
        }

        // The game's volumes are 15-bit: 0x3FFF is full scale.
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

        uint32_t readU32(const uint8_t *p)
        {
            return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) |
                   (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24);
        }

        // PS-ADPCM data of one IMC entry: the channels take turns, `span` 16-byte blocks each.
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

        // One channel of a playing stream.
        struct Voice
        {
            bool playing = false;  // started and not stopped; its position keeps counting
            bool sounding = false; // still has data to play
            bool loop = false;
            std::shared_ptr<const SoundData> data;
            uint32_t channel = 0;
            uint32_t step = 0x1000; // source samples per output sample, 12-bit fraction
            uint32_t block = 0;
            bool endAfterBlock = false;
            int16_t decoded[kSamplesPerBlock]{};
            uint32_t decodedPos = kSamplesPerBlock;
            int32_t hist1 = 0, hist2 = 0;
            std::array<int32_t, 4> window{};
            uint32_t phase = 0;
            uint64_t samples = 0; // consumed since the start

            // A seamless section change: from block `switchAt` on, play `next` from its start.
            std::shared_ptr<const SoundData> next;
            uint64_t switchAt = UINT64_MAX;
        };

        struct Channel
        {
            Voice voice;
            uint16_t volume = 0;
            uint16_t pan = 0x3FFF;
            float left = 0.0f, right = 0.0f;
        };

        // The channels a stream plays on. The sections of a song share one group, so its volumes and
        // pans carry across section changes.
        struct Group
        {
            std::array<Channel, kMaxChannels> channels;
            int count = 0;
        };

        struct Stream
        {
            bool open = false;
            bool wholeSound = false; // a short sound rather than streamed music
            bool loop = false;
            bool ownsGroup = false;
            bool playing = false;
            int channels = 0, rate = 0, interleave = 0, rows = 0;
            std::shared_ptr<Group> group;
            std::shared_ptr<SoundData> data;
        };

        struct LibraryEntry
        {
            char name[9]{};
            uint32_t offset = 0;
        };

        // The movie's PCM on its way to the mixer: blocks of 256 left then 256 right samples.
        struct Movie
        {
            bool playing = false;
            float gain = 0.0f;
            std::vector<uint8_t> ring;
            size_t read = 0, write = 0, queued = 0;
            uint32_t frame = 0; // within the block at `read`
        };

        class Engine
        {
        public:
            void start()
            {
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

            void request(uint32_t fno, const uint8_t *send, uint32_t sendSize, uint8_t *reply)
            {
                std::array<uint32_t, 4> args{};
                std::array<uint8_t, 64> raw{};
                if (send)
                {
                    std::memcpy(args.data(), send, std::min<size_t>(sendSize, sizeof(args)));
                    std::memcpy(raw.data(), send, std::min<size_t>(sendSize, raw.size()));
                }
                std::array<uint32_t, 4> answer{};
                std::unique_lock<std::mutex> lock(m_mutex);
                handle(fno, args, raw, answer, lock);
                std::memcpy(reply, answer.data(), sizeof(answer));
            }

            // Once per game frame: the song clock the game reads until the next one, in whole
            // blocks, never going back. It trails the mix by what the sound card still holds.
            void latchSongClock()
            {
                std::lock_guard<std::mutex> lock(m_mutex);
                if (!m_clockGroup || !m_clockRunning)
                    return;
                const Voice &v = m_clockGroup->channels[0].voice;
                const int64_t samples = static_cast<int64_t>(v.samples) - m_clockOffsetFrames * v.step / 0x1000;
                if (samples > 0)
                    m_clock = std::max<uint32_t>(m_clock, static_cast<uint32_t>(samples / kSamplesPerBlock) * kSamplesPerBlock);
            }

            // The movie player's volume setting is applied twice on the PS2's output path.
            void setMovieVolume(uint16_t volume)
            {
                std::lock_guard<std::mutex> lock(m_mutex);
                const float gain = volumeGain(volume);
                m_movie.gain = gain * gain;
            }

            void movieOpen(uint32_t bytes)
            {
                std::lock_guard<std::mutex> lock(m_mutex);
                const float gain = m_movie.gain;
                m_movie = Movie{};
                m_movie.gain = gain;
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
            // Decoding
            // ---------------------------------------------------------------------------------

            void fetchBlock(Voice &v)
            {
                v.decodedPos = 0;
                if (v.endAfterBlock)
                {
                    v.sounding = false;
                    v.endAfterBlock = false;
                }
                if (v.next && v.samples / kSamplesPerBlock == v.switchAt)
                {
                    v.data = std::move(v.next);
                    v.next.reset();
                    v.switchAt = UINT64_MAX;
                    v.block = 0;
                    v.sounding = true;
                }
                const SoundData *data = v.data.get();
                const uint8_t *b = nullptr;
                if (v.sounding && data)
                {
                    if (!data->ready.load(std::memory_order_acquire))
                        ++v.block; // still loading: silence, keeping time
                    else if (v.block >= data->blocksPerChannel || !(b = data->block(v.channel, v.block)))
                        v.sounding = false;
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
                for (uint32_t i = 0; i < kSamplesPerBlock; ++i)
                {
                    const int32_t nibble = (b[2 + i / 2] >> ((i & 1u) * 4u)) & 0xF;
                    int32_t sample = static_cast<int16_t>(static_cast<uint16_t>(nibble << 12)) >> shift;
                    sample += (v.hist1 * kPos[filter] + v.hist2 * kNeg[filter] + 32) >> 6;
                    sample = clamp16(sample);
                    v.hist2 = v.hist1;
                    v.hist1 = sample;
                    v.decoded[i] = static_cast<int16_t>(sample);
                }
                // Bit 0 of a block's flags marks the end of the sound.
                if (flags & 1u)
                {
                    if (v.loop)
                        v.block = 0;
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
                v.phase += v.step;
                while (v.phase >= 0x1000u)
                {
                    v.phase -= 0x1000u;
                    if (v.decodedPos >= kSamplesPerBlock)
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

            void render(int16_t *out, uint32_t frames)
            {
                std::lock_guard<std::mutex> lock(m_mutex);
                for (uint32_t f = 0; f < frames; ++f)
                {
                    float mix[2] = {};
                    for (const std::shared_ptr<Group> &group : m_groups)
                    {
                        for (int c = 0; c < group->count; ++c)
                        {
                            Channel &channel = group->channels[c];
                            if (!channel.voice.playing)
                                continue;
                            const float s = static_cast<float>(voiceSample(channel.voice));
                            mix[0] += s * channel.left;
                            mix[1] += s * channel.right;
                        }
                    }
                    mix[0] *= m_master;
                    mix[1] *= m_master;
                    if (m_movie.playing && m_movie.queued >= 0x400u)
                    {
                        const size_t at = m_movie.read + m_movie.frame * 2u;
                        mix[0] += m_movie.gain * static_cast<float>(static_cast<int16_t>(m_movie.ring[at] | (m_movie.ring[at + 1u] << 8)));
                        mix[1] += m_movie.gain * static_cast<float>(static_cast<int16_t>(m_movie.ring[at + 0x200u] | (m_movie.ring[at + 0x201u] << 8)));
                        if (++m_movie.frame == 256u)
                        {
                            m_movie.frame = 0;
                            m_movie.read = (m_movie.read + 0x400u) % m_movie.ring.size();
                            m_movie.queued -= 0x400u;
                        }
                    }
                    out[f * 2u] = static_cast<int16_t>(clamp16(static_cast<int32_t>(std::lrint(mix[0]))));
                    out[f * 2u + 1u] = static_cast<int16_t>(clamp16(static_cast<int32_t>(std::lrint(mix[1]))));
                    updateStreams();
                }
            }

            // A section change takes effect, and a stream that has played out stops reporting as playing.
            void updateStreams()
            {
                if (m_change.to >= 0 && m_music >= 0)
                {
                    const Voice &lead = m_streams[m_music].group->channels[0].voice;
                    if (lead.samples / kSamplesPerBlock >= m_change.at)
                    {
                        SOUND_TRACE("section change: stream %d -> %d\n", m_music, m_change.to);
                        m_streams[m_music].playing = false;
                        m_streams[m_change.to].playing = true;
                        std::replace(m_streaming.begin(), m_streaming.end(), m_music, m_change.to);
                        m_music = m_change.to;
                        m_musicStart = m_change.at;
                        m_change = SectionChange{};
                    }
                }
                for (size_t i = 0; i < m_streaming.size();)
                {
                    Stream &s = m_streams[m_streaming[i]];
                    if (s.group->channels[0].voice.sounding)
                    {
                        ++i;
                        continue;
                    }
                    s.playing = false;
                    if (m_streaming[i] == m_music)
                        m_music = -1;
                    m_streaming.erase(m_streaming.begin() + static_cast<std::ptrdiff_t>(i));
                }
            }

            // ---------------------------------------------------------------------------------
            // Requests
            // ---------------------------------------------------------------------------------

            // The game's stereo pan law: pan 0x3FFF is the centre; away from it the far side fades.
            static void applyVolume(Channel &c)
            {
                const int32_t vol = c.volume;
                const uint32_t pan = c.pan;
                int32_t left = 0, right = 0;
                if (vol != 0)
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
                c.left = volumeGain(static_cast<uint16_t>(left));
                c.right = volumeGain(static_cast<uint16_t>(right));
            }

            Stream *stream(uint32_t id)
            {
                return id < kMaxStreams && m_streams[id].open ? &m_streams[id] : nullptr;
            }

            Channel *channel(uint32_t id, uint32_t index)
            {
                Stream *s = stream(id);
                return s && index < static_cast<uint32_t>(s->group->count) ? &s->group->channels[index] : nullptr;
            }

            int freeStream() const
            {
                for (int i = 0; i < kMaxStreams; ++i)
                    if (!m_streams[i].open)
                        return i;
                return -1;
            }

            const LibraryEntry *lookup(const char *name) const
            {
                for (int i = 0; i < m_libraryCount; ++i)
                    if (std::strcmp(m_library[i].name, name) == 0)
                        return &m_library[i];
                return nullptr;
            }

        public:
            bool openLibrary(const std::string &path)
            {
                std::lock_guard<std::mutex> lock(m_mutex);
                std::vector<uint8_t> head;
                if (!Loader::readRange(path, 0, 4, head) || head.size() < 4)
                {
                    std::fprintf(stderr, "[sound] cannot read library %s\n", path.c_str());
                    return false;
                }
                m_libraryPath = path;
                m_libraryCount = std::clamp(static_cast<int>(readU32(head.data())), 0, kLibraryEntries);
                std::vector<uint8_t> table;
                (void)Loader::readRange(m_libraryPath, 4, m_libraryCount * 32ull, table);
                table.resize(static_cast<size_t>(m_libraryCount) * 32u, 0u);
                for (int i = 0; i < m_libraryCount; ++i)
                {
                    LibraryEntry &e = m_library[i];
                    const uint8_t *p = table.data() + static_cast<size_t>(i) * 32u;
                    for (int k = 0; k < 8; ++k)
                        e.name[k] = static_cast<char>(std::toupper(p[k]));
                    e.name[8] = '\0';
                    e.offset = readU32(p + 0x10);
                }
                SOUND_TRACE("library %s: %d entries\n", path.c_str(), m_libraryCount);
                return true;
            }

        private:

            // Opens a library entry. `shareWith` >= 0 plays it on that stream's channels.
            bool open(const char *name, bool wholeSound, bool loop, int shareWith, std::array<uint32_t, 4> &answer)
            {
                const int id = freeStream();
                const LibraryEntry *entry = lookup(name);
                if (id < 0 || !entry)
                {
                    std::fprintf(stderr, "[sound] cannot open %s\n", name);
                    answer[0] = 1;
                    return false;
                }
                std::vector<uint8_t> header;
                if (!Loader::readRange(m_libraryPath, entry->offset, 16u, header) || header.size() < 16u ||
                    readU32(header.data()) < 1u || readU32(header.data()) > static_cast<uint32_t>(kMaxChannels))
                {
                    std::fprintf(stderr, "[sound] %s: bad header\n", name);
                    answer[0] = 1;
                    return false;
                }
                Stream s;
                s.open = true;
                s.wholeSound = wholeSound;
                s.loop = loop;
                s.channels = static_cast<int>(readU32(header.data()));
                s.rate = static_cast<int>(readU32(header.data() + 4));
                s.interleave = std::max(static_cast<int>(readU32(header.data() + 8)), 1);
                s.rows = static_cast<int>(readU32(header.data() + 12)) / s.channels;
                if (shareWith >= 0 && stream(static_cast<uint32_t>(shareWith)))
                    s.group = m_streams[shareWith].group;
                else
                {
                    s.group = std::make_shared<Group>();
                    s.group->count = s.channels;
                    s.ownsGroup = true;
                    m_groups.push_back(s.group);
                }
                auto data = std::make_shared<SoundData>();
                data->channels = static_cast<uint32_t>(s.channels);
                data->span = static_cast<uint32_t>(s.interleave);
                data->blocksPerChannel = static_cast<uint32_t>(s.rows) * data->span;
                s.data = data;
                const uint64_t bytes = static_cast<uint64_t>(s.rows) * s.channels * data->span * 16u;
                if (wholeSound)
                {
                    if (Loader::readRange(m_libraryPath, entry->offset + 16ull, bytes, data->bytes))
                        data->ready.store(true, std::memory_order_release);
                }
                else
                {
                    m_loader.enqueue({data, m_libraryPath, entry->offset + 16ull, bytes});
                }
                answer = {0u, static_cast<uint32_t>(s.channels), static_cast<uint32_t>(s.rate), static_cast<uint32_t>(id)};
                m_streams[id] = std::move(s);
                SOUND_TRACE("open %s as %d: %d ch, %d Hz, %d rows%s\n", name, id, m_streams[id].channels, m_streams[id].rate,
                            m_streams[id].rows, shareWith >= 0 ? ", shared channels" : "");
                return true;
            }

            void stopGroup(Group &group)
            {
                for (int c = 0; c < group.count; ++c)
                {
                    Voice &v = group.channels[c].voice;
                    v.playing = false;
                    v.sounding = false;
                    v.next.reset();
                    v.switchAt = UINT64_MAX;
                }
            }

            void forgetStreaming(int id)
            {
                m_streaming.erase(std::remove(m_streaming.begin(), m_streaming.end(), id), m_streaming.end());
                if (m_music == id)
                {
                    m_music = -1;
                    m_change = SectionChange{};
                }
            }

            void stop(uint32_t id)
            {
                Stream *s = stream(id);
                if (!s)
                    return;
                stopGroup(*s->group);
                if (!s->wholeSound && s->playing)
                {
                    m_clockRunning = false;
                    s->playing = false;
                    forgetStreaming(static_cast<int>(id));
                }
            }

            void close(uint32_t id)
            {
                Stream *s = stream(id);
                if (!s)
                    return;
                if (s->ownsGroup)
                {
                    stop(id);
                    m_groups.erase(std::remove(m_groups.begin(), m_groups.end(), s->group), m_groups.end());
                }
                forgetStreaming(static_cast<int>(id));
                *s = Stream{};
            }

            void closeAll()
            {
                for (int i = kMaxStreams - 1; i >= 0; --i)
                    close(static_cast<uint32_t>(i));
            }

            void play(uint32_t id, bool streaming, bool resetClock)
            {
                Stream *s = stream(id);
                if (!s)
                    return;
                if (streaming)
                {
                    m_clockRunning = true;
                    forgetStreaming(static_cast<int>(id));
                    m_streaming.push_back(static_cast<int>(id));
                    m_music = static_cast<int>(id);
                    m_musicStart = 0;
                }
                if (resetClock)
                {
                    m_change = SectionChange{};
                    m_clock = 0;
                }
                const uint32_t step = static_cast<uint32_t>((static_cast<uint64_t>(s->rate) * 0x1000u + kOutputRate / 2u) / kOutputRate);
                for (int c = 0; c < s->group->count; ++c)
                {
                    Voice &v = s->group->channels[c].voice;
                    v = Voice{};
                    v.playing = true;
                    v.sounding = true;
                    v.loop = s->loop;
                    v.data = s->data;
                    v.channel = static_cast<uint32_t>(c);
                    v.step = std::min<uint32_t>(step, 0x3FFFu);
                }
                s->playing = true;
                SOUND_TRACE("play %u%s%s\n", id, streaming ? " streaming" : "", resetClock ? ", clock reset" : "");
            }

            // Queues the next section of the playing song: from `position` blocks into the current
            // section, its channels continue with the start of `id`.
            void queueSection(uint32_t id, uint32_t position)
            {
                Stream *next = stream(id);
                if (!next || m_music < 0)
                    return;
                m_change.to = static_cast<int>(id);
                m_change.at = m_musicStart + position;
                Group &group = *m_streams[m_music].group;
                for (int c = 0; c < group.count; ++c)
                {
                    group.channels[c].voice.next = next->data;
                    group.channels[c].voice.switchAt = m_change.at;
                }
                SOUND_TRACE("next section: stream %u at block %u\n", id, position);
            }

            void resetAll()
            {
                closeAll();
                m_groups.clear();
                m_change = SectionChange{};
                m_clockGroup.reset();
                m_clockRunning = false;
                m_clock = 0;
            }

            void handle(uint32_t fno, const std::array<uint32_t, 4> &a, const std::array<uint8_t, 64> &raw,
                        std::array<uint32_t, 4> &answer, std::unique_lock<std::mutex> &lock)
            {
                const auto text = [&]() { const auto *p = reinterpret_cast<const char *>(raw.data()); return std::string(p, std::find(p, p + 0x3E, '\0')); };
                const bool share = raw[0x3E] != 0;
                const int base = raw[0x3F];
                const uint32_t command = fno & 0xFFFFFF00u;
                switch (command)
                {
                case 0x0100: // start of a sound session
                    resetAll();
                    break;
                case 0x0E00: // end of a sound session
                    resetAll();
                    break;
                case 0x3100: // the driver's threads: nothing to do
                case 0x3200:
                case 0xF100:
                    break;
                case 0x1B00:
                case 0x0D00:
                    closeAll();
                    break;
                case 0x1400:
                    open(text().c_str(), false, false, share ? base : -1, answer);
                    break;
                case 0x1500:
                    open(text().c_str(), true, base != 0, -1, answer);
                    break;
                case 0x1F00:
                    close(a[0]);
                    break;
                case 0x2000: // wait for a stream's data
                case 0x2100:
                    if (Stream *s = stream(a[0]); s && !s->wholeSound && a[2] != 0)
                    {
                        const std::shared_ptr<SoundData> data = s->data;
                        lock.unlock();
                        m_loader.wait(data);
                        lock.lock();
                    }
                    answer[0] = command == 0x2100 ? 1u : 0u;
                    break;
                case 0x2200: // preload finished?
                    break;
                case 0x0800:
                    play(a[0], a[1] != 0, a[2] != 0);
                    break;
                case 0x0900:
                    answer[0] = a[0] < kMaxStreams && m_streams[a[0]].open && m_streams[a[0]].playing ? 1u : 0u;
                    break;
                case 0x0A00:
                    stop(a[0]);
                    break;
                case 0x5100:
                    if (Channel *c = channel(a[0], a[1]))
                    {
                        c->volume = static_cast<uint16_t>(a[2]);
                        applyVolume(*c);
                    }
                    break;
                case 0x5200:
                    if (Channel *c = channel(a[0], a[1]))
                    {
                        c->pan = static_cast<uint16_t>(a[2]);
                        applyVolume(*c);
                    }
                    break;
                case 0x5600:
                    answer[0] = m_clock;
                    break;
                case 0x5700: // which stream drives the song clock
                    if (Stream *s = stream(a[0]))
                        m_clockGroup = s->group;
                    m_clock = 0;
                    m_clockRunning = false;
                    m_change = SectionChange{};
                    break;
                case 0x5800:
                    queueSection(a[0], a[1]);
                    break;
                case 0x5C00:
                    m_change = SectionChange{};
                    break;
                case 0x5A00:
                    m_master = volumeGain(static_cast<uint16_t>(a[0] >> 1));
                    break;
                default:
                    std::fprintf(stderr, "[sound] request 0x%x not handled\n", command);
                    answer[0] = 1;
                    break;
                }
            }

            struct SectionChange
            {
                int to = -1;
                uint64_t at = 0;
            };

            std::mutex m_mutex;
            Loader m_loader;
            int64_t m_clockOffsetFrames = 0;
            float m_master = volumeGain(0x3FFF);

            std::array<Stream, kMaxStreams> m_streams{};
            std::vector<std::shared_ptr<Group>> m_groups;
            std::vector<int> m_streaming; // streams playing out, to notice when they end
            int m_music = -1;             // the stream section changes apply to
            uint64_t m_musicStart = 0;    // block of its channels' position where it began
            SectionChange m_change;

            std::array<LibraryEntry, kLibraryEntries> m_library{};
            int m_libraryCount = 0;
            std::string m_libraryPath;

            std::shared_ptr<Group> m_clockGroup;
            bool m_clockRunning = false;
            uint32_t m_clock = 0;

            Movie m_movie;
        };

        Engine &engine()
        {
            // Never destroyed: the device thread may still pull from it while the process exits.
            static Engine *instance = new Engine();
            return *instance;
        }
    }

    void start() { engine().start(); }

    void request(uint32_t fno, const uint8_t *send, uint32_t sendSize, uint8_t *reply)
    {
        engine().request(fno, send, sendSize, reply);
    }

    bool openLibrary(const std::string &hostPath) { return engine().openLibrary(hostPath); }

    void latchSongClock() { engine().latchSongClock(); }
    void setMovieVolume(uint16_t volume) { engine().setMovieVolume(volume); }
    void movieOpen(uint32_t bytes) { engine().movieOpen(bytes); }
    void moviePlay(bool playing) { engine().moviePlay(playing); }
    void movieClear() { engine().movieClear(); }
    uint32_t movieFree() { return engine().movieFree(); }
    void moviePut(const uint8_t *data, uint32_t bytes) { engine().moviePut(data, bytes); }
}
