#pragma once

#include <cstdint>
#include <string>

// Forward declarations for render context structures
struct RenderContext;
struct TextureObject;
struct LightObject;

// The game's rendering, native (docs/native-render.md).
namespace port::render
{
    void start();
    void shutdown();
    
    // Called at each vblank to process the render context
    void processRenderContext();
    
    // Switches the renderer to native mode
    void enableNativeRenderer();
    
    // Checks if the native renderer is currently enabled
    bool isNativeRendererEnabled();
    
    // Render a batch of vertices using OpenGL
    void drawBatch(const uint8_t* batchData, size_t batchSize);
    
    // Handle texture binding
    void bindTexture(const TextureObject* texture);
    
    // Handle lighting state
    void setLights(const LightObject* lights, int count);
    
    // Handle matrix operations
    void loadMatrix(const float* matrix);
    void multMatrix(const float* matrix);
    
    // Handle viewport settings
    void setViewport(int x, int y, int width, int height);
    
    // Handle state changes
    void enableState(uint32_t state);
    void disableState(uint32_t state);
}