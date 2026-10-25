# GPU renderer (GS on OpenGL)

The first step toward native rendering: the PS2's Graphics Synthesizer is drawn with OpenGL (through
raylib's GL 3.3 context) instead of the multithreaded CPU rasterizer. It lives in the PS2Recomp fork
(`ps2xRuntime/src/lib/gs/gs_gpu_backend.{h,cpp}`).

It is the default renderer:

```sh
PS2X_GS_SCALE=3 build/runtime/ps2xRuntime/ps2EntryRunner iso/SLUS_202.94   # 3x internal resolution
PS2X_GS_BACKEND=cpu build/runtime/ps2xRuntime/ps2EntryRunner iso/SLUS_202.94  # CPU rasterizer
```

| variable | effect |
|---|---|
| `PS2X_GS_BACKEND=cpu` | use the CPU rasterizer instead |
| `PS2X_GS_SCALE=N` | internal resolution N x (1-8, default 2) |
| `PS2X_GS_GPU_VERIFY=1` | run the CPU rasterizer on a copy of GS memory too and compare every presented frame; stats every 5 s, side-by-side PPMs (GPU, CPU, 8x difference) of the first bad frames into `PS2X_DUMP_FRAMES`. Runs at 1x unless `PS2X_GS_SCALE` is set (upscaled presentation shows single fields, which never match the CPU's woven frames) |
| `PS2X_GS_GPU_VERIFY_TICK=t` | also dump the comparison for vsync ticks t..t+3 |
| `PS2X_GS_GPU_PRESENT=0` | present through the CPU path (download + CPU presentation) |
| `PS2X_GS_GPU_STATS=1` | every 10 s: counts of unsupported features and of target uploads/downloads by reason |
| `PS2X_GS_CENSUS=<file>` | (any backend) histogram of the GS features the game uses |

## Design

**Threads.** The guest thread only queues commands (draws, transfers, CLUT loads, presents). The
host (window) thread executes them in `PumpHostThread()` between frames, since that is where the GL
context lives. Calls that return data (VRAM reads, local->host transfers) wait for the host; FINISH
does not. The queue is bounded (the guest waits beyond 64 batches).

**GS memory stays the source of truth.** A `GSCpuBackend` over the same memory runs transfers, CLUT
loads, local->local copies and fallback presentation. Render targets live on the GPU and are synced
through GS memory by page: written back before anything else reads or writes their pages (render to
texture, transfers), uploaded again before the next draw after GS memory changed under them.

**Textures** are read in the shader from a GPU copy of all 4 MiB of GS memory (a 1024x1024 R32UI
texture updated per dirty 8 KiB page), addressed through the same page lookup tables `GSMem` uses,
so the shader reads exactly what the CPU rasterizer reads. CLUTs become 256-entry palette textures
resolved with the CPU's `LookupCLUT` (CSM, CSA, TEXA).

**Pixel pipeline.** The fragment shader mirrors `GSCpuBackend`: wrap modes (incl. region), point or
4-tap bilinear filtering with the CPU's rounding, TFX/TCC, fog, alpha test, FBA; depth is written as
`z / 2^32` into a 32-bit float depth buffer (constant-Z primitives exact). Blending
`(A - B) * C / 128 + D` is mapped onto GL blend equations with dual-source factors; the cases GL
cannot express (destination alpha as the factor, `1 + f`) are counted.

**Presentation.** The display buffer is snapshotted on the GPU. At 1x the game's 224-line field
buffers are woven like the CPU `FieldWeaver`; upscaled, each field is already a whole picture with
more lines than a woven frame, so the newest field is shown alone with the lower field moved up
half a line. The host loop draws the result into the 4:3 window. Frame dumps come from the GPU image.

## Status (2026-10-24)

- Verified against the CPU rasterizer through the menus, tutorial and stage 1 battle: mean difference
  0.12/255 per channel and no frame with more than 0.25% of pixels off by more than 16 (Gitaroo uses
  only CT32 frames, Z32, T4/T8/CT32 textures and five blend modes there).
- Stages 2-10 (`tools/run/stage.sh`, about 15 minutes each, the verify shadow runs on the threaded
  CPU rasterizer so the game keeps its speed): stages 5-10 have no bad frame. Known differences:
  - Stage 2: 2 frames of the fading "Now Loading" text (edge sampling precision).
  - Stage 3: the water and the intro close-up blend with alpha above 128 (factor above 1.0), which
    GL clamps. Those draws now run in two passes, Cs*f - Cd*(f-1) for the part above 1, which took
    the stage from 2375 bad frames to about 300-400. The rest are pixels where Cs*f passes 255 and
    the GS saturates before subtracting; matching that needs the shader to read the framebuffer.
  - Stage 4: the tracer rays are triangles with vertices behind the camera, sent with S, T and Q
    clamped to +-FLT_MAX. GL overflowed interpolating them; scaling a triangle's S/T/Q by a power of
    two (exact for S/Q, T/Q) brought the stage 4 battle from 1227 to 899 bad frames. The rest are
    the same triangles, where the CPU rasterizer's float interpolation overflows too: both draw
    noise there, and only a PCSX2 capture of that scene can say what the GS draws.
    `PS2X_GS_GPU_STQ_RAW=1` turns the scaling off.
- Speed: tutorial 60 fps; stage 1 battle 50-60 fps at 1x and 3x (CPU rasterizer: 30-59). The guest
  thread (EE + VU1, about 30% of it VU1) is now the limit; the host thread has headroom.
- Not drawn yet: points and lines (Gitaroo uses neither). Not exact: per-bit FBMSK, DATE, AFAIL
  modes other than KEEP, PABE, destination-alpha blending (none used by Gitaroo).
- Finding a difference: `PS2X_GS_GPU_PRIMLOG=N` (or `bad` for the first failing frame) with
  `PS2X_DUMP_FRAMES` writes every draw of that frame to `primlog.txt` next to the GPU | CPU |
  difference dump; `tools/gs/primlog_suspects.py` ranks the GS states under the differing pixels.
- Fixed by verification: palettes are one texture rewritten in place (a palette cache flushed every
  256 CLUT loads left batched draws sampling a deleted texture: black rectangles in menus).

## Next

- Native 3D (the AC5 `rn_vp` route): replace the VU1 1B68 program with native code that builds GPU
  meshes from the data the game uploads, checked against the lifted microprogram's output.
- Widescreen: `PS2X_WIDESCREEN=1` applies the community 16:9 patch at run time (FOV scale at
  0x26FC70 written before `AirNgp_LoadIdentity` reads it; the 2D projection width 640 -> 480 by
  wrapping the renderer method 0x1CF810) and presents 16:9. The picture is anamorphic, as on a PS2:
  the game still renders 640 wide, so the GPU scale factor is what gives it detail. A true wide
  render target (and a correctly proportioned, centred 2D layer) is still to do.
