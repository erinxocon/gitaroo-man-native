# The VU1 mesh renderer (entry 0x1B68) and its native replacement

Gitaroo Man has one VU1 microprogram (AiCube / AIR.NGP, see [vu1-lifter.md](vu1-lifter.md)). Entry
0x1B68 does 99.9% of its work: it transforms, lights, clip-tests and packs one batch of vertices
into a GIF packet. This page describes what it computes, as read from the microcode
(`python3 tools/vu1lift/vu1dis.py --from 0x1B68 --to 0x2830 --b1b70 0x1B80`), and the native C++
that now replaces it (`external/PS2Recomp/ps2xRuntime/src/lib/vu/ps2_vu1_native.cpp`).

## Inputs

The EE's VIF stream uploads a batch to the double-buffered input area and starts the program with
MSCAL 0x1B68. XTOP = batch address, XITOP = vertex count n. Each vertex is 4 qwords:

| qword | contents |
|---|---|
| +0 | position x y z w |
| +1 | normal x y z |
| +2 | S T Q |
| +3 | R G B A (floats) |

Constants, loaded earlier by the other entries (0x0668, 0x0720, 0x0768, 0x0788, 0x07D0):

| register / qword | use |
|---|---|
| vf1-vf4 | model-view-projection (columns: clip = vf1*x + vf2*y + vf3*z + vf4*w) |
| vf8-vf10 xyz | normal rotation; vf8.w colour clamp, vf9.w colour scale, vf10.w alpha scale |
| vf11, vf12 | screen scale and offset (x y z, w = fog) |
| vf13-vf15 | light directions (4 lights, one per lane) |
| vf16-vf19 | light colours, one per light |
| data[25] | emissive colour, added after lighting |
| data[26] x / y | output buffer address / the other buffer (swapped after each kick) |
| data[28].y | clip-path continuation (0x2548 strips, 0x26A0 lists, 0x24B8) |
| data[29].x | continuation after a batch (0x27C0 = XGKICK; others chain more work) |
| data[42] | GIF tag template (NLOOP gets n added) |
| data[202].x | extra pass (0x1100, 0x1A98, 0x0C08: environment/second layer) |

## Modes

The EE selects one of four bodies by rewriting the branch at 0x1B70 in the VIF stream
(`FLUSHE; MPG 1 pair`). Share of 1B68 calls in the stage 1 battle census:

| target | share | colour |
|---|---|---|
| 0x1B80 | 67% | `min(RGBA * (max(lights(R*n), 0), vf10.w) + emissive, vf8.w)` |
| 0x2108 | 23% | `RGBA * (vf9.w, vf9.w, vf9.w, vf10.w)` (prelit) |
| 0x2280 | 6% | as 0x2108; the rotated normal is normalized (ERLENG) and stored |
| 0x1E10 | 4% | as 0x1B80 with the rotated normal normalized first |

where `lights(n) = vf16*L.x + vf17*L.y + vf18*L.z + vf19*L.w` and `L = vf13*n.x + vf14*n.y + vf15*n.z`.

For every vertex, all modes:

- `clip = MVP * position`, `flags = CLIP(clip.xyz, clip.w)`, `Q = 1 / clip.w`
- out STQ = `STQ.xyz * Q`, RGBA = `FTOI0(colour)`, XYZF = `FTOI4((clip.xyz * Q, clip.w) * vf11 + vf12)`
- in place, for the clipper and later passes: +0 = clip position, +1 = (rotated normal, Q),
  +2.w = clip flags, +3 = colour (float); and +96 = the input colour.

The output buffer holds the GIF tag then STQ/RGBA/XYZF per vertex (PACKED).

## Paths through the program (stage 1 battle census, 10.5M calls)

| path | share | what happens |
|---|---|---|
| fast | 50% | no vertex outside the frustum: tag, XGKICK (0x27C0), swap output buffers |
| culled | 43% | every vertex outside the same plane (AND of flags != 0): end, nothing drawn |
| extra pass | 4% | data[202] != 0: JALR into another routine that draws a second layer |
| clipped | 2.5% | some vertex outside: the clipper (0x04C0) and emitter (0x0688) at data[28].y |

## Native replacement

`VU1Lifted::runNative` runs whenever the lifted program would start at 0x1B68. It computes the
batch with VU arithmetic (round toward zero, operands with denormals flushed and Inf clamped, the
exact-result rule for results near zero or the range limits, the VU's MAX/MINI/FTOI/CLIP/DIV/
ERLENG), then either commits everything the microprogram would have written -- the in-place
vertex data, the colour copies, the output buffer including the pipelined loop's three leftover
register stores, the GIF tag, the kick, the buffer swap -- and the registers it leaves behind
(the software-pipelined loop's prologue/epilogue, the loads past the end of the batch, the ERLENG
it starts on the normal after the batch), or declines without touching anything and the lifted
program runs. It declines for clipped and extra-pass batches, more than 23 vertices, and buffer
layouts where its read-then-write order could differ from the program's.

Verification: `PS2X_VU1_VERIFY=1` runs the interpreter and the lifted/native path from the same state
and compares registers, VU1 memory and packets. Stage 1 battle: 13.2M runs, 0 mismatches.
`PS2X_VU1_NATIVE_BREAK=1` (flip one colour bit per batch) is flagged on 83% of all VU1 runs, the
share native handles. `PS2X_VU1_NATIVE=0` turns it off.

Speed, stage 1 battle with scripted input (fps per 2 s window, 2x GPU renderer): the heavy section
(vsync ticks ~5100-7400) went from 30 fps to a locked 60; the busiest later part from 50 to 55-58.

## Next

- The clip path and the extra-pass routines in native code (6.5% of calls).
- GPU meshes: the native loop has the float screen positions before FTOI4; passing them to the GPU
  GS backend alongside the packet (as AC5's `pkt_put_xyzf` does) removes the 1/16-pixel vertex
  snapping at high internal resolutions, and is the hook for drawing these batches as real meshes.
