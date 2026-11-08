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
2. Clipped (2-6%) and extra-pass (0.1-5%) batches: done by describing the lifted program's packets
   (below). Left: extra passes that change more than STQ, and the clipper itself in native code.
3. With vertices on the GPU: no 1/16-pixel snapping, progressive full-height frames instead of
   fields, a real 16:9 view (widen the projection rather than squeeze), MSAA.
4. Mesh caching at the `DrawElements32` level (vertex arrays have stable guest addresses; skinned
   ones are rewritten by the CPU each frame) once the shader path exists.

### Step 1: done, opt-in (`PS2X_VU1_GPU=1`)

Drawn 1B68 batches carry a `NativeVu1Batch` (gs_types.h) next to their GIF packet: the 24 input
vertices as loaded, the constants above, the render mode and whether the template tag writes XYZ2 or
XYZF2. The GS frontend tags each vertex it decodes from such a packet with the batch and vertex
index, and the OpenGL backend draws those triangles from a mesh VAO, with the batch constants in a
buffer texture (18 vec4 per batch), transformed and lit in the vertex shader. The packet itself is
unchanged, so the CPU rasterizer, the verifier and everything else in the frame see what they saw.

The shader reproduces the VU bit for bit where the GPU has fp64 (`GL_ARB_gpu_shader5`): each
multiply and add is done in double and truncated to float (the VU rounds toward zero), with
`precise` so the compiler keeps the order, and a sum's TwoSum error deciding when the double sum
was itself rounded; overflow is +-FLT_MAX and denormals are zero (inputs too); a multiply-add near
the ends of the range is decided by its exact value like the VU's (stage 4 has matrices near
FLT_MAX: -FLT_MAX + 2e40 is FLT_MAX, and 1 / FLT_MAX flushes to 0); reciprocal and square root
search the neighbouring floats for the truncated result because GPU double division is not
correctly rounded. Then saturating FTOI4/FTOI0 and the GS's view of the words: 16-bit X/Y, 24-bit Z
above 4 (or the 32-bit XYZ2 word), fog, colour bytes, and Q = 0 read as 1. Batches whose STQ is
beyond 2^64 (the title logo's +-FLT_MAX texture coordinates) stay on the packet path.

The other two ways out of 1B68 are covered without new VU code, by describing packets the lifted
program sends and checking the description against every word:
- Clipping (0x2418): the clipper sends one packet per triangle through the emitter (0x0688), which
  reads clip position, Q, STQ and colour per vertex from the batch's own slots or the clipper's
  output (266/326). Each kicked packet is matched against those places by recomputing it with the
  emitter's arithmetic; on a match the GPU gets those vertices (`NativeVu1Batch::emitted`) and
  does the emitter's arithmetic in the shader.
- Extra pass (data[202], a small texgen program at 0x0C08/0x1100/0x1A98): it rewrites the output's
  STQ before the kick. If the kicked packet's colours and positions are the native batch's, the
  batch goes to the GPU with the packet's STQ (`stqFromPacket`).
A packet that does not match is drawn from the packet as before.

Checks:
- `PS2X_VU1_GPU_VERIFY=1`: every native vertex is computed on the CPU with the shader's formulas
  and compared with the words in the packet, and the shader's outputs are read back by transform
  feedback and compared with the vertex the GS decoded.
- `PS2X_GS_GPU_VERIFY=1` with `PS2X_VU1_GPU=1`: whole frames against the CPU rasterizer.

Results (all ten stages, 900 s each with `PS2X_GS_GPU_VERIFY=1 PS2X_VU1_GPU=1 PS2X_VU1_GPU_VERIFY=1`):

| stage | vertices checked | differ (CPU check / GPU read-back) | frames | bad frames | clipper packets described | extra-pass packets described / not |
|---|---|---|---|---|---|---|
| 1 | 579M | 0 / 0 | 50213 | 0 | 1.8M, all | 1.10M / 76k |
| 2 | 445M | 0 / 0 | 33623 | 0 | 1.8M, all | 4.38M / 15k |
| 3 | 654M | 0 / 0 | 41126 | 0 | 13.0M, all | 3.75M / 117k |
| 4 | 193M | 0 / 0 | 39658 | 34 | 6.4M, all | 195k / 1.4k |
| 5 | 675M | 0 / 0 | 51284 | 0 | 15.7M, all | 929k / 251k |
| 6 | 805M | 0 / 0 | 42032 | 0 | 6.9M, all | 66k / 0 |
| 7 | 990M | 0 / 0 | 40046 | 0 | 9.0M, all | 3.93M / 0.9k |
| 8 | 726M | 0 / 0 | 52622 | 0 | 15.6M, all | 197k / 0 |
| 9 | 755M | 0 / 0 | 49033 | 0 | 25.6M, all | 398k / 1.04M |
| 10 | 969M | 0 / 0 | 52439 | 0 | 5.4M, all | 197k / 0 |

Stage 4's bad frames are the ray effect's, which differ between the GPU and CPU rasterizers with or
without this path (21-28 per run on the packet path). Extra-pass packets that are not described
are mostly batches that also clip (their first packet is the clipper's) and, in stage 9, a texgen
that also changes colour or position; those draw from the packet. Speed was the same with the
first version (60 fps locked on both paths in the stage 1 battle).

`PS2X_VU1_GPU_SUBPIXEL=1` draws these vertices at their float screen positions instead of the GS's
1/16 pixel (for internal resolutions above 1x; not for verify runs).

Debug: `PS2X_VU1_GPU_MODES=<mask>` limits the path to some render modes (bit 0 1B80, 1 1E10,
2 2108, 3 2280); `PS2X_VU1_GPU_NEAREST=1` uses the round-to-nearest shader (what a GPU without fp64
gets).

Stays on the GS path: `CopySurface` and anything sampling the framebuffer, texture/CLUT uploads,
clears, movies, debug text.
