cmake_minimum_required(VERSION 3.21)

# Gitaroo Man as its own program: the recompiled game (build/recomp, from tools/build/recomp.sh),
# our code (src/) and our own main(). The PS2Recomp runtime is linked as a library for whatever has
# not been replaced by native code yet, and is not modified for the port.
#
# ps2EntryRunner, the runtime's stock runner, stays available as the reference build of the same
# game under emulation (`ninja ps2EntryRunner`); it is not built by default.
project(GitarooMan LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(GITAROO_RECOMP_DIR "${CMAKE_CURRENT_SOURCE_DIR}/build/recomp" CACHE PATH "Recompiler output")

set(PS2X_BUILD_RUNTIME ON CACHE BOOL "" FORCE)
set(PS2X_BUILD_RECOMP OFF CACHE BOOL "" FORCE)
set(PS2X_BUILD_ANALYZER OFF CACHE BOOL "" FORCE)
set(PS2X_BUILD_TEST OFF CACHE BOOL "" FORCE)
set(PS2X_BUILD_STUDIO OFF CACHE BOOL "" FORCE)
set(PS2X_ENABLE_AGRESSIVE_LOGS OFF CACHE BOOL "" FORCE)
set(PS2X_ENABLE_DEBUG_UI OFF CACHE BOOL "" FORCE)
set(PS2X_GENERATED_DIR "${GITAROO_RECOMP_DIR}" CACHE PATH "" FORCE)
set(PS2X_GAME_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/src" CACHE PATH "" FORCE)
add_subdirectory(external/PS2Recomp external EXCLUDE_FROM_ALL)

file(GLOB GITAROO_RECOMP_SOURCES CONFIGURE_DEPENDS "${GITAROO_RECOMP_DIR}/*.cpp")
# src/*.cpp also goes into the reference build; src/port/ is the port's alone.
file(GLOB GITAROO_SHARED_SOURCES CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp")
file(GLOB_RECURSE GITAROO_NATIVE_SOURCES CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/src/port/*.cpp")
set(GITAROO_PORT_SOURCES ${GITAROO_SHARED_SOURCES} ${GITAROO_NATIVE_SOURCES})

# Game functions defined natively in src/port/: the recompiled versions are left out of the port
# (they stay in the reference build), so every call to them, direct or through the function table,
# reaches the native one.
set(GITAROO_NATIVE_FUNCTIONS
    # Sound (src/port/sound/game_functions.cpp)
    _sceSifLoadModule_0x131410
    sceSifBindRpc_0x12f870
    FUN_0023e690_0x23e690
    sceSdRemoteInit_0x1361e0
    sceSdRemote_0x136320
    FUN_0023f660_0x23f660
    FUN_0023f5e0_0x23f5e0
    FUN_0023f4b0_0x23f4b0
    FUN_0023f540_0x23f540
    FUN_0023f3f0_0x23f3f0
    FUN_0023eff0_0x23eff0)
foreach(GITAROO_NATIVE_FUNCTION IN LISTS GITAROO_NATIVE_FUNCTIONS)
    list(REMOVE_ITEM GITAROO_RECOMP_SOURCES "${GITAROO_RECOMP_DIR}/${GITAROO_NATIVE_FUNCTION}.cpp")
endforeach()

add_executable(gitaroo-man ${GITAROO_RECOMP_SOURCES} ${GITAROO_PORT_SOURCES})
target_include_directories(gitaroo-man PRIVATE "${GITAROO_RECOMP_DIR}" "${CMAKE_CURRENT_SOURCE_DIR}/src")
target_link_libraries(gitaroo-man PRIVATE ps2_runtime)

# The generated code is huge and rarely changes: batch and precompile it as the stock runner does,
# and leave our own files out of the batches so editing one does not rebuild 32 others.
set_target_properties(gitaroo-man PROPERTIES UNITY_BUILD ON UNITY_BUILD_BATCH_SIZE 32)
set_source_files_properties(${GITAROO_PORT_SOURCES} PROPERTIES SKIP_UNITY_BUILD_INCLUSION ON)
set(GITAROO_RUNTIME_INCLUDE "${CMAKE_CURRENT_SOURCE_DIR}/external/PS2Recomp/ps2xRuntime/include")
set(GITAROO_PCH_HEADERS
    "${GITAROO_RUNTIME_INCLUDE}/ps2_runtime_macros.h"
    "${GITAROO_RUNTIME_INCLUDE}/ps2_runtime.h")
# Absent on a first configure, before tools/build/recomp.sh has run.
foreach(GITAROO_GENERATED_HEADER IN ITEMS ps2_recompiled_functions.h ps2_recompiled_stubs.h)
    if(EXISTS "${GITAROO_RECOMP_DIR}/${GITAROO_GENERATED_HEADER}")
        list(APPEND GITAROO_PCH_HEADERS "${GITAROO_RECOMP_DIR}/${GITAROO_GENERATED_HEADER}")
    endif()
endforeach()
list(APPEND GITAROO_PCH_HEADERS
    "${GITAROO_RUNTIME_INCLUDE}/ps2_syscalls.h"
    "${GITAROO_RUNTIME_INCLUDE}/ps2_stubs.h")
target_precompile_headers(gitaroo-man PRIVATE ${GITAROO_PCH_HEADERS})

ps2x_stage_ffmpeg_runtime_dlls(gitaroo-man)
if(CMAKE_BUILD_TYPE STREQUAL "Release" OR CMAKE_BUILD_TYPE STREQUAL "RelWithDebInfo")
    # EnableFastReleaseMode reads IPO_SUPPORTED from the scope that calls it.
    include(CheckIPOSupported)
    check_ipo_supported(RESULT IPO_SUPPORTED OUTPUT IPO_ERROR)
    EnableFastReleaseMode(gitaroo-man)
endif()
