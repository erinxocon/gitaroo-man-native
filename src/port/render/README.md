# Native Render Engine

This directory contains the native rendering implementation for Gitaroo Man. The goal is to replace the PS2 GS emulation layer with a native OpenGL-based renderer.

## Current Status

The VU1 GPU acceleration (Step 1) has already been implemented. This module implements the remaining steps in the native renderer plan:

1. Reconstruct context state from the native render context
2. Draw reconstructed batches with OpenGL into the renderer's own framebuffer
3. Switch to using the native renderer instead of the GS path

## Implementation Plan

The implementation will:
- Create a new OpenGL context for rendering
- Intercept calls to the game's render context methods
- Reconstruct all necessary state (matrices, textures, lights, etc.) 
- Render batches directly to an OpenGL framebuffer
- Provide a mechanism to switch between native and GS rendering modes

## Key Components

- `render_engine.h/cpp`: Main engine interface and implementation
- `opengl_renderer.h/cpp`: OpenGL-specific rendering code
- `context_reconstructor.h/cpp`: Reconstructs render context state for OpenGL