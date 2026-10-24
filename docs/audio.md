# Audio output and latency

Gitaroo Man is a rhythm game: it judges each press against its own clock, so every millisecond
between the game producing a sample and the speaker playing it puts the player's presses that
much late. This page records where that time goes in the port and how it is kept small.

## Path

1. The game's IOP modules (SDRDRV, MSTREAM, LIBSD) run under the IOP emulator and drive the
   emulated SPU2 (`ps2xIOP/src/emulator/core/iop_spu2.cpp`), which mixes 48 kHz stereo.
2. The IOP has no clock of its own: it advances as the game thread accounts EE cycles
   (`EeScheduler::accountCycles` -> `PS2Runtime::advanceIopEeCycles`). SPU2 output therefore
   arrives in bursts of about one frame (~15 ms), and stops whenever the game thread stops.
3. `Spu2HostOutput` (`ps2xRuntime/src/lib/ps2_audio.cpp`) buffers it in a ring that a
   miniaudio device callback drains.

## Latency budget (stage 1 battle, PulseAudio on PipeWire)

| stage | before | now |
|---|---|---|
| ring (`PS2X_TRACE_AUDIO_LATENCY=1` prints it) | 107-215 ms, creeping up, snapped back at 250 ms | 30-40 ms |
| device buffer | 75 ms (raylib's shared device, 3600 frames) | 15 ms (own device, 3 x 5 ms periods) |

The ring is held at a target (`PS2X_AUDIO_LATENCY_MS`, default 40) by playing up to 0.5% faster
or slower (linear resampling, inaudible); after an underrun it re-primes to the target, and if it
gets 30 ms over the target (the emulator catching up after a load) it drops straight back.

The SPU2 output has its own miniaudio device, opened through the miniaudio that raylib 5.5 builds
and exports (the `MA_NO_*` options in `ps2_audio.cpp` must match raylib's `raudio.c`, since they
change `ma_device`'s layout). `PS2X_AUDIO_PERIOD_MS` sets the period; `PS2X_AUDIO_RAYLIB=1` uses a
raylib stream on raylib's device instead.

## Dropouts

Anything that blocks the game thread for longer than the ring holds is a dropout, because the IOP
stops with it. `PS2X_TRACE_STALLS=1` reports vblanks the game thread reached more than 20 ms late
and its waits on the GS host thread. The one found so far was the frame dumper encoding PNGs on the
host thread (~50 ms each), which filled the GS queue; it now encodes on a worker thread. With it
fixed the stage 1 battle has no underruns.

## Video, for comparison

The host loop draws within ~0.1 ms of each guest present (measured: the guest's vblank clock and
the host's 60 Hz loop end up phase-locked), and pad input is polled right after each draw, so the
video path adds well under a frame to what the PS2 itself has.
