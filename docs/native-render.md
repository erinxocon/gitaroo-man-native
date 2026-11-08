# Native rendering: findings and plan

Where Gitaroo's drawing comes from, measured, and how it can move from GS emulation to native GPU
meshes. Background research: the AC5 port's `rn_vp` (VU1-program-level replacement with a GPU
vertex shader), OpenGOAL (VU renderers rewritten per DMA bucket), RT64/N64Recomp, PCSX2's hardware
renderer.

## How the game draws (render-context census)

`PS2X_RENDER_CENSUS=1` wraps every method of the AIR.NGP render context (vtable 0x2624F0) and logs
call counts and callers every 600 frames; `python3 tools/analysis/render_census.py logs/<run>.log`
names them. Stage 1 battle, scripted input, per frame:

| what | calls/frame | notes |
|---|---|---|
| `DrawElements32` (+0x144) | 18 | every 3D mesh; all from display-list replay (`AirNgp_RunCallbacks`), u32 indices into one vertex array (`SetVertexArray` 21) |
| `Begin`/`End`, `Vertex3f`/`Color4f`/`TexCoord2f` | 38 / 170 vertices | HUD, notes, trace line, effects: immediate mode, 16 call sites |
| `DrawArrays`, `DrawElements16`, `DrawVertexArray` | 0 | unused here |
| `BindTexture` 20, `BlendFunc` 20, `SetAlphaBlendRaw` 15, `TexEnv` 32, `Enable`/`Disable` 26/27, `DepthFunc` 48 | | state per draw |
| `PushMatrix`/`PopMatrix`/`PushMultMatrix`, `LoadMatrix`, `Translate`/`Rotate`/`Scale` | 15-32 | matrix stack |
| lights (`LightOpD4`, `LightDirection`, `LightColor*`) | 1.7 | per frame, not per mesh |
| `CopySurface` | 0.3 | one screen-copy effect: must stay on the GS path |
| `Flush` | 1 (from `SwapBuffers`) | the frame's VIF packets are sent once per frame |

So: little state, few mesh draws, and both meshes and immediate-mode quads end up as the same
VU1 batches (entry 1B68, at most 24 vertices) in one per-frame packet stream.

## Plan

1. **Native transform at the VU1 batch level** (the AC5 route). For drawn batches (no clipping,
   no extra pass) hand the GPU backend the batch's input vertices (position, normal, STQ, colour as
   floats) and constants (MVP vf1-4, normal rotation vf8-10, screen scale/offset vf11-12, lights
   vf13-19, emissive, mode) instead of the GIF packet, and transform and light them in a vertex
   shader. The native C++ loop keeps producing VU1 memory and registers exactly (and the packet,
   for the CPU rasterizer when verifying), so nothing else in the frame changes and ordering with
   GS state and other draws is the packet stream's.
   - Verify vertex by vertex: transform feedback of the shader's outputs against the native loop's
     (XYZF after FTOI4, STQ, RGBA after FTOI0), with a stated tolerance; then frames against the
     CPU rasterizer (`PS2X_GS_GPU_VERIFY`).
2. Clipped (2-6%) and extra-pass (0.1-5%) batches stay on the packet path until 1 is proven, then
   GPU clipping (`gl_ClipDistance`) and the second-layer routines (0x0C08/0x1100/0x1A98).
3. With vertices on the GPU: no 1/16-pixel snapping, progressive full-height frames instead of
   fields, a real 16:9 view (widen the projection rather than squeeze), MSAA.
4. Mesh caching at the `DrawElements32` level (vertex arrays have stable guest addresses; skinned
   ones are rewritten by the CPU each frame) once the shader path exists.

Stays on the GS path: `CopySurface` and anything sampling the framebuffer, texture/CLUT uploads,
clears, movies, debug text.
