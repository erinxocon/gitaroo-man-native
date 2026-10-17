# Gitaroo Man (SLUS_202.94): executable map (partial)

This is a first pass. Symbols are in `config/symbols/agent.txt`.

## Memory layout
| Range | Contents |
|---|---|
| 0x100000-0x11FF60 | game code (main, album page system, module helpers, Array templates) |
| 0x11FF60-0x146000 | MW C++ runtime, Sony libs (libgraph/dma/pkt/mpeg/ipu/kernel/cdvd/pad/mtap/mc/scf/sdr), newlib |
| 0x146000-0x244400 | game modules (gm*) + Xeios engine (kernel 0x16F000, particles 0x17A000, AIR.NGP 0x1BF000-0x1D9FFF, AIR 0x1DB4F0, xg 0x1DC440-0x1EE000) |
| 0x244400-0x25C740 | rodata/data: strings, GUIDs (0x251200...), vtables (0x25F5E0-0x264450) |
| 0x25C740-0x25F5DC | `__sinit_*` static constructors (ctor + `__register_global_object`) |
| 0x264680-0x311280 | bss. gp = 0x26C470 (set in `entry`) |

Code is 16-byte aligned (game, MW) and 8-byte aligned (Sony/gcc libs). Ghidra's original pass missed about 1540 functions that are only reached through vtables or function-pointer tables. They were recovered in `build/ghidra-symbols`, where the count is 5180 (the original count was 3641). Methods that end in `jr t9` are tail calls through a vtable.

## Boot and the main loop
`entry` sets gp, calls SetupThread/SetupHeap, then `main` (0x100BB0). `main`:
1. Calls malloc/free to pre-size the heap (0x1BECD70).
2. Calls `__init_cpp_rts_tables`, then `Main_InitRenderer`.
3. Calls `Iop_LoadModules` (`Iop_RebootAndInit` with IOPRP214.IMG, then SIO2MAN, MTAPMAN, MCMAN, MCSERV, PADMAN, LIBSD, SDRDRV, MSTREAM).
4. Parses `cdrom0:\ALBUMDEF.TXT` with `AlbumDef_ParseLine`. The file uses the directives `PAGE_MOD <id> <MODULE_TYPE>`, `PAGE_ARG` and `PAGE_RET <ret> <nextPage>`.
5. Initializes pad, multitap and memory card.
6. Runs the page state machine. Each page's module function is called as `Fn(argc, argv)`, and its return value is mapped through the page's PAGE_RET table to the next page. A value below 0 exits.

Module type ids and their entry points:

| id | Module type | Entry point |
|---|---|---|
| 1 | TITLE | `gmTitle_Main` 0x234610 |
| 2 | SINGLELOADER | none |
| 3 | MENU | none |
| 4 | ALBUMMENU | `gmMenuAlbum_Main` 0x1BD280 |
| 5 | SINGLE_PLYR_GAME | `gmSinglePlyrGame_Main` 0x175C70 |
| 6 | TUTORIAL | `gmTutorial_Main` 0x209980 |
| 7 | MULTI_PLYR_GAME | 0x192F70 |
| 8 | MOVIE | `gmMovie_Main` 0x10A760 |
| 9 | MULTI_MENU | 0x218AD0 |
| 10 | MINIGAME | none |
| 11 | CHARA_COLLECT | 0x1F8510 |
| 12 | SAVELOAD | 0x153DA0 |
| 13 | OPTIONS | 0x23A9E0 |
| 14 | VIEWER | 0x150B70 |
| 15 | CREDITS | none |
| 16 | TEST | 0x10ACE0 |
| 17 | MEMCARDCHECK | 0x172EF0 |
| 18 | SOUNDPREVIEW | 0x1F95F0 |

"Memory used entering %s" printfs mark the entry and exit of each module. Command-line switches remain from the host builds and are printed as "Argument syntax".

## Renderer (COM-like, GUID-keyed)
- `Xe_CreateInstance(clsid, iid)` (0x16FA10) looks the class up in a factory registry. Factories register through `Xe_RegisterClassFactory` from `__sinit`.
- **AIR system** (`g` 0x264680, vtable 0x262FA0/0x262FB4): `Air_LoadDriver(CLSID AIR.NGP)`, `SetParam`, `CreateSurface({flags,w,h,psm})`, `GetRenderContext`, and debug print/rect. It forwards each call to the driver.
- **AIR.NGP device / render context** (`g` 0x264684 = gp-0x7DEC, vtable 0x2624F0, 106 methods). Methods are called as `ctx->vtbl[(off-8)/4]` and the API is OpenGL-like:
  - Enable and Disable are at +0x50 and +0x54.
  - Viewport, Scissor, Frustum and Ortho are at +0x58 to +0x64.
  - The matrix stack runs from +0x98 to +0xC8.
  - Lights are at +0xCC to +0xFC.
  - Textures are at +0x10C to +0x128.
  - The GS ALPHA register is set at +0x12C and the primitive type at +0x130.
  - Vertex arrays and draws are at +0x13C to +0x150.
  - Immediate-mode `Begin`/`Vertex`/`Normal`/`Color`/`TexCoord`/`End` are at +0x154 to +0x194.
  - SetRenderTarget is +0x38 (FRAME), SetDepthBuffer is +0x3C (ZBUF), and Read/Write/CopySurface are +0x40 to +0x48 (BITBLT).
- Driver interface (vtable 0x2626EC): Initialize (installs interrupt handlers and stat counters), Shutdown, Create/DestroySurface, Set/GetParam, CreateObject (textures, arrays), DrawDebugText.
- GS packets are built into a VIF1 DIRECT stream at `*(u32**)0x264834`. `AirDma_PutGsRegAD` (0x1D9CF0) adds an A+D register write. FUN_001d9f00, FUN_001d9eb0 and FUN_001d9e80 open, close and kick the packet. The VIF1 ring DMA is at FUN_001d9f90. Debug dumps are `AirDma_DumpD1State` and `AirDma_DumpD2State`, and `Air_DumpLockupInfo` prints a GS/DMA register dump on lockup.
- Texture manager: `texmanager.cpp` around 0x1D6700-0x1D7B40. Vertex arrays: `array.cpp`/`arraygl.cpp` at 0x1D4EE0 and 0x1D9080.

## xg scene graph (.XGM)
At 0x1DC440 and later. The type registry is a set of descriptor tables `{0, "xgTypeName", create, destroy}` at 0x24CF18 and later (xgColor, xgDagMesh, xgBone, xgEnvelope, xgMaterial, xgTexture, the interpolators, and so on). The text and binary graph loaders are `xg_LoadGraphFile` and `xg_ReadGraphHeader`, with the parsers at 0x1E58F0 and 0x1E6060. Textures come from `.pic` (Softimage) or `.imx` files via `xgTexture_LoadFile`. Node attributes connect through output and input attributes; see `xgNode.cpp` around 0x1E86D0.

## Files and formats
- `.XGM`/`.XG`: scene graphs.
- `.IMX`: images.
- `.CHC`: note charts (tags CHCH, GMLS, GMLP, parsed around 0x117130-0x1179B0).
- `.SSQ`: stage description.
- `.WEB`: control web for multiplayer/viewer.
- `.IMC`/`.IMS`: sound banks and songs.
- `.PSS`: movies (libmpeg; decode thread strings at 0x25C2A0).
- `.ICO`: memory card icons (PUMA_*.ICO, `/BASLUS-20294USER`).

All paths are `/PROJECTS/STDATA/US/...`. `Path_ToCdrom` converts them to `cdrom0:\...;1`.

## Audio, input, memory card (not yet named)
- Audio goes through SDRDRV.IRX via `sceSdRemote` (libsdr) and MSTREAM.IRX for streaming. The audio-information printfs are at 0x25C060.
- Pad objects are DS2, DS and PB ("Deleted pad obj").
- Memory-card code is around 0x155730-0x15A120 (PgmStart, Save Single, Album Save/Load, Format), with a worker thread whose strings are at 0x25C4C0.

## Open issues
- config/symbols/sdk.txt misidentifications: `sceCccIsValidUTF8` at 0x1DA930 and 0x1DBE30 is really an AIR-local `operator new` thunk to malloc. `__dt__Q23std9exceptionFv` at 0x16FD20, 0x1D7830, 0x1D7C30, 0x1E3ED0, 0x1EAE40 and 0x1EB900 are unrelated class destructors; the real one is at 0x120390.
