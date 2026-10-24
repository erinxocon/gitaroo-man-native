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
| `PS2X_GS_GPU_VERIFY=1` | run the CPU rasterizer on a copy of GS memory too and compare every presented frame; stats every 5 s, side-by-side PPMs (GPU, CPU, 8x difference) of the first bad frames into `PS2X_DUMP_FRAMES` |
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
  only CT32 frames, Z32, T4/T8/CT32 textures and five blend modes there; later stages are not
  reachable by the scripted runs yet).
- Speed: tutorial 60 fps; stage 1 battle 50-60 fps at 1x and 3x (CPU rasterizer: 30-59). The guest
  thread (EE + VU1, about 30% of it VU1) is now the limit; the host thread has headroom.
- Not drawn yet: points and lines (Gitaroo uses neither). Not exact: per-bit FBMSK, DATE, AFAIL
  modes other than KEEP, PABE, destination-alpha blending (none used by Gitaroo).
- Fixed by verification: palettes are one texture rewritten in place (a palette cache flushed every
  256 CLUT loads left batched draws sampling a deleted texture: black rectangles in menus).

## Next

- Native 3D (the AC5 `rn_vp` route): replace the VU1 1B68 program with native code that builds GPU
  meshes from the data the game uploads, checked against the lifted microprogram's output.
- Widescreen: the community 16:9 patch (FOV at 0x26FC70 and HUD fixes) applied as recompiler
  patches, rendered into a wider target.
