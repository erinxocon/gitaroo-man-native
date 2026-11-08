# Native audio: census and plan

Goal (CLAUDE.md): replace the emulated sound stack -- the IOP emulator running SDRDRV, MSTREAM
and LIBSD, and the SPU2 model under them -- with host code that serves the game's sound requests
directly. This page records what the game asks for, in what format the data is, and what the
replacement has to do. The emulated stack stays as the reference to check against.

## Tools

- `PS2X_TRACE_MSTREAM=1`: every RPC to MSTREAM (SID 0x1563) with caller, sizes, the first 8 send
  and reply words, the vsync tick and host time. `PS2X_TRACE_RPC=1`: every SIF RPC.
  `PS2X_TRACE_IOP_IMPORTS=1`: every import an IOP module calls (LIBSD's included).
- `tools/ioplift/irx_ghidra.sh`: relocates LIBSD, SDRDRV and MSTREAM to their load addresses
  (`irx_symbols.py`, names for imports, LIBSD exports and pointer-reached code), imports them into
  the Ghidra project `build/ghidra/IopSound` and writes pseudo-C to `build/irx/<NAME>.c`.
  Derived from the game: never commit the outputs.

## What the game uses

Two IOP services, both reached through SIF RPC:

- **SDRDRV** (SID 0x80000701, Sony's remote LIBSD for the EE's libsdr; fno & 0xFFF0 picks the
  LIBSD call): `sceSdInit` (0x8000) and `sceSdSetParam` (0x8010, master volumes) at start-up, and
  the movie sound: `sceSdBlockTrans` (0x80E0) with core 0, mode 0x13 (write, loop), IOP address
  0x8AB40, 0x6000 bytes starts SPU2 AutoDMA from a PCM ring the EE fills over SIF DMA (mode 2
  stops it), and the movie player polls `sceSdBlockTransStatus` (0x8100) for the position --
  822k polls in a 5-minute run with movies.
- **MSTREAM** (SID 0x1563, iNiS's "MultiStream_driver"): everything else -- music, sound effects
  and the song clock. The command is `fno & 0xFFFFFF00`; arguments are words of the send buffer;
  the reply is 16 bytes at 0x54E94: result, then for opens the stream header's channel count and
  sample rate and the stream id.

MSTREAM commands seen (stage 1 from boot, scripted input; counts in "Census" below):

| fno | handler | meaning (from the IRX and the traces) |
|---|---|---|
| 0x0100 | init | create the driver's threads and state |
| 0x0E00 | sleep | stop and free everything |
| 0x3100 / 0x3200 | | start / stop the playback threads and the vblank clock handler |
| 0x1A00 | 0x4B138 | open a library (`cdrom0:\PROJECTS\STDATA\US\...\X.IMC`): read its entry table |
| 0x1B00 | 0x4B2A0 | close the library |
| 0x1400 | 0x4B4A8 | open a stream by entry name (song sections `INT`, `A1`, `B1aT`...): header, SPU buffers, voices |
| 0x1500 | 0x4C064 | open a "local" sound by entry name (effects `SELECT`, `OK`...: loaded whole) |
| 0x1F00 | 0x4C90C | close a stream |
| 0x2000 / 0x2100 / 0x2200 | 0x4C7C4 / 0x4ED94 / | preload a stream; 0x2200 polls for the preload to end |
| 0x0800 | 0x4D4AC | play: sets the SPU IRQ address for refills, keys the voices on, optionally resets the song clock |
| 0x0900 | | stream status (polled every frame) |
| 0x0A00 | 0x4D694 | stop a stream |
| 0x0D00 | 0x4CA24 | stop all |
| 0x5100 / 0x5200 | 0x4E8A0 / 0x4E90C | set channel volume / pan (stream, channel, 0..0x7FFF; pan 0x3FFF centre) |
| 0x5600 | | read the song clock (polled every frame) |
| 0x5700 | 0x4D830 | choose the stream whose voice drives the song clock, reset it |
| 0x5800 | 0x4DC38 | queue (stream, sample position, flag): the event thread starts that stream when the playing one gets there (seamless section changes) |
| 0x5C00 | 0x4DC24 | clear the event queue |
| 0xF100 | | print free memory |

## How MSTREAM plays

- A stream is a sequence of interleaved PS-ADPCM channels (1-8) read from an IMC library on disc.
  Each channel plays on its own SPU2 voice (pitch 0x1000 for 48 kHz, 0x0EB3 for 44.1 kHz) from a
  ring of 2 or 3 buffers in SPU RAM. MSTREAM reads chunks from the CD into IOP RAM, uploads them
  with `sceSdVoiceTrans`, and refills on the SPU IRQ (the IRQ address follows the voice).
- Volume and pan per channel are set per frame by the game (0x5100/0x5200 with stream and
  channel; the channel picks a voice from the stream's voice table). MSTREAM turns (volume, pan)
  into the voice's left/right volume (0x4E6D8): stereo, volume/2 on the near side and the far side
  reduced by how far the pan is from 0x3FFF; mono (0x5B00 0), a centre-weighted attenuation.
- Song clock: a vblank handler reads the clock stream's voice address (`sceSdGetAddr`, NAX) and
  turns the distance into samples played (0x4D830, 28 samples per 16-byte block). The game reads
  it with fno 0x5600 every frame. So on the PS2 too the clock moves in vblank steps.
- Section changes: the game queues the next section with 0x5800; a thread waits until the playing
  stream is near its end and starts the queued one.

## IMC files

`PROJECTS/STDATA/US/*/SONGDATA/STnn.IMC` (one per stage, 18-271 MB), `SOUND/*.IMC` (menus),
`TITLE/TITLE.IMC`:

- u32 entry count, then 32-byte entries: name (up to 8 characters, NUL-terminated by byte 8 at
  the latest; MSTREAM upper-cases the first 8 bytes), words, the stream's byte offset (+0x10), a
  word, an unused word (MSTREAM keeps the file handle there), and a kind (+0x1C): 0 for streams,
  2 for sounds loaded whole (effects, jingles).
- At each stream's offset, a 16-byte header: channels (1-8), sample rate (48000, 44100, 22050;
  a rate MSTREAM has no pitch for, such as ST10's 22000, plays silent), the interleave in 16-byte
  ADPCM blocks (MSTREAM's SPU buffer size; below 0x300 it uses 3 IOP buffers, else 2) and the
  length in interleave chunks, all channels together. Then the channels' PS-ADPCM data in turn,
  one chunk of `interleave` blocks (0x3000 bytes for the music) per channel (16-byte blocks:
  shift/filter, flags, 14 bytes of nibbles), the first block of each channel silent. Every sound
  ends with an end flag (1) followed by a self-looping silent block (7).
- Stage songs are cut into sections (ST01: `INTs INT A1 B1aT B1aL ... C1a-d ENDs ENDa-d`, 8-46 s),
  all 8 channels at 48 kHz, interleave 0x300; jingles and voices (`Lose Win Result Pause Select OK
  Cancel P1atms ... Win_P1-6`) are 1-2 channels. In ST01's A1, channels 0-1 are a stereo pair
  (the backing, -12 dBFS), 2 and 4 carry the same mono part, as do 3 and 5, and 6-7 are a second
  stereo pair (`tools/audio/imc.py wav` decodes them). The battle pans 0 left, 1 right, 2-5
  centre and starts 4 and 5 silent, so the duplicated parts are faded between per player action;
  which is which is still to be listened to.

## Native engine

Native sound is the default. It replaces the three drivers (`ps2xRuntime/src/lib/ps2_native_sound.cpp`):
their module loads succeed without the IOP, the runtime binds and answers SIDs 0x1563 and
0x80000701 itself, and the emulated SPU2 is disconnected from the audio device. The IOP emulator
still runs the other modules (pad, memory card) until those become host services too. With
`PS2X_NATIVE_AUDIO=0` the drivers run on the IOP emulator as before, which is the reference.

- **Data.** A library open reads the IMC entry table. A stream open (0x1400) reads its header and
  queues its data for a loader thread, which reads it into memory (a section is opened seconds
  before it plays; the synchronous preload, 0x2000, waits for it). A sound loaded whole (0x1500) is
  read at once, with the loop flags MSTREAM writes over the first and last blocks of each chunk.
- **Voices.** Each stream channel is a host voice on the voice MSTREAM would have allocated (first
  free of 24 per core; streams on core 0, sounds on core 1, a section shares its first section's
  voices). It decodes PS-ADPCM with the SPU's filters, follows the data's loop and end flags and
  steps by the SPU pitch for the stream's rate (Catmull-Rom between samples). Volume and pan go
  through MSTREAM's pan law into the voice's left/right volume, and MSTREAM's per-voice volume
  record is kept, so a voice keeps its stored pan across streams as on the PS2.
- **Mix.** Like the SPU2's dry path: voices summed per core, core 0 plus the movie input through
  its master volume into core 1, core 1's master volume, 16-bit clamps at the same points.
  Reverb is not mixed: its volume stays 0 in this game (a warning prints if it is ever set).
- **Section changes.** MSTREAM switches a playing stream to the queued section (0x5800) at an
  exact block: the voices do the same, from the queued stream's first block, at that block.
- **Status.** The status word (0x0900, polled every frame) changes on MSTREAM's refill schedule:
  a round each time the lead voice reaches a chunk boundary of the current stream, which switches
  to the queued section once the next upload would cross the change (1-2 chunks, up to ~0.9 s,
  before it is heard) and ends the stream after its last chunk round. The engine keeps that
  schedule (with the counters MSTREAM keeps, including the IOP buffer count that gives a stream
  with interleave 0x300 two more rounds than one below), since the game reacts to the status.
- **Song clock.** At each vblank the clock voice's position since key-on, minus the audio still
  buffered ahead of the speaker, in whole 28-sample blocks, never decreasing; 0x5600 returns the
  last vblank's value, as MSTREAM's vblank handler did.
- **Movies.** SDRDRV's AutoDMA ring: the ring is copied from IOP memory when the transfer starts
  and kept current from the EE's SIF DMA writes into it; the mixer plays it (blocks of 256 left
  then 256 right samples) and `sceSdBlockTransStatus` reports the half and the address two blocks
  ahead of the sample playing, like the SPU2's DMA.
- **Clock.** By default the audio device's callback pulls the mix (`PS2X_NATIVE_AUDIO_CLOCK`
  unset): sound and song clock follow the device, not the game thread, so a stall in the game no
  longer stalls the music. `PS2X_NATIVE_AUDIO_CLOCK=ee` mixes on emulated time instead, like the
  SPU2 it replaces, which makes runs comparable sample for sample with the reference.
  `PS2X_NATIVE_AUDIO_CLOCK_OFFSET_MS` overrides how far the song clock trails the mix.
- `PS2X_TRACE_NATIVE_AUDIO=1` prints opens, plays, section changes and status changes.

Not handled (never used by the game): MSTREAM's opens by file path or from EE memory (0x1000,
0x1100, 0x1300) reply failure with a warning; SDRDRV calls other than init, parameters, switches,
voice and block transfers print a warning.

### Checking it

`tools/audio/mstream_diff.py <reference.log> <native.log>` compares two `PS2X_TRACE_MSTREAM=1`
runs: the requests and replies in order, and each stream's status changes with the song clock at
the time. `tools/audio/wav_compare.py <reference.wav> <native.wav>` compares `PS2X_AUDIO_DUMP`
recordings window by window (best lag, levels, residual).

Results (2026-11-08, `stage1_battle.txt` from boot, 900 s, against `PS2X_NATIVE_AUDIO=0`):

| | emulated clock (`ee`) | device clock (default) |
|---|---|---|
| MSTREAM requests and arguments | all 1493 identical, in order | all 1493 identical, in order |
| replies | identical except words holding the song clock | same |
| status changes | the same 27, in order; song clock at each within 450 samples | the same 27; within ~1000 samples (the 720-frame device offset) |
| song clock rate | 800 samples per vblank, as the reference | 800 per vblank |
| mixed output | title, menus, effects and battle music bit-exact once aligned (residual -inf dB); the rest differs only where the game moved a volume or played an effect at another moment | no discontinuities the reference does not have |

Movies (boot without input, 300 s, SDRDRV's ring): bit-exact once aligned, no discontinuities.
Between the two runs the movie player resyncs by whole laps of its 0x6000-byte ring (128 ms)
about every 12 s, a difference in how fast video and sound advanced in each run rather than in the
samples. The first version read the ring live and played blocks the player had already
overwritten for the next lap (3500 clicks); it now copies each block when the SPU2's DMA would.

### Open

- Listening: which duplicated part is which, and whether the device-clocked song clock (trailing
  the mix by the device buffer) feels as the PS2 does; `PS2X_NATIVE_AUDIO_CLOCK_OFFSET_MS` tunes it.
- Paths the scripted runs do not reach: winning, the versus mode, stages 2-10 (same commands per
  the census).
- The IOP emulator still loads the pad and memory card modules; once those are host services it
  goes, and with it the SPU2 model's stepping.

## Census

MSTREAM commands per run (stage runs start from the save, play out with no input, and lose):

| fno | stage 1 from boot | stage 3 | stage 5 | stage 10 | boot with movies |
|---|---|---|---|---|---|
| 0x5600 clock | 11562 | 6452 | 6631 | 5566 | |
| 0x0900 status | 11046 | 4503 | 4704 | 3619 | |
| 0x5100 volume | 908 | 833 | 977 | 843 | 616 |
| 0x5200 pan | 299 | 225 | 369 | 234 | 8 |
| 0x0A00 stop | 87 | 82 | 118 | 84 | 4 |
| 0x2200 preload poll | 68 | 35 | 35 | 35 | |
| 0x0800 play | 33 | 38 | 29 | 33 | 2 |
| 0x1400 open stream | 21 | 29 | 47 | 30 | |
| 0x1500 open local | 19 | 34 | 34 | 34 | 4 |
| 0x1F00 close | 17 | 31 | 31 | 31 | 4 |
| 0x1A00 / 0x1B00 library | 4 / 3 | 8 / 7 | 8 / 7 | 8 / 7 | 2 / 2 |
| 0x0100 / 0x0E00 init / sleep | 4 / 3 | 8 / 7 | 8 / 7 | 8 / 7 | 2 / 2 |
| 0x0D00 stop all | 3 | 7 | 7 | 7 | 2 |
| 0x5700 clock stream | 4 | 6 | 6 | 6 | |
| 0x2000 / 0x2100 preload | 4 / 2 | 6 / 1 | 6 / 1 | 6 / 1 | |
| 0x5800 queue / 0x5C00 clear | 4 / 3 | 2 / 1 | 2 / 1 | 2 / 1 | |
| 0x3100 / 0x3200 threads | 2 / 3 | 1 / 2 | 1 / 2 | 1 / 2 | 2 (0x3200) |
| 0xF100 memory print | 2 | 1 | 1 | 1 | |

Never seen: 0x1000, 0x1100, 0x1300 (other open variants), 0x5300, 0x5A00, 0x5B00 (so the stereo
pan law is the one in use). LIBSD calls MSTREAM makes: `sceSdSetParam` (all voice and master
parameters), `sceSdVoiceTrans` (uploads), `sceSdSetSwitch` (key on/off), `sceSdSetAddr`/
`sceSdGetAddr` (start, loop, IRQ addresses; NAX for the clock), `sceSdSetCoreAttr`/`GetCoreAttr`,
`sceSdInit`, `sceSdSetTransIntrHandler`, `sceSdSetSpu2IntrHandler`.
