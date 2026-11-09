#pragma once

#include <cstdint>

// Forward declarations for OpenGL types (these would come from actual OpenGL headers)
struct GLFWwindow;

namespace port::render::opengl
{
    // Initialize OpenGL context and renderer
    bool initialize();
    
    // Shutdown the OpenGL renderer
    void shutdown();
    
    // Render a frame using OpenGL
    void renderFrame();
    
    // Clear the framebuffer
    void clearFramebuffer();
    
    // Set up the viewport
    void setViewport(int x, int y, int width, int height);
    
    // Draw a batch of vertices
    void drawBatch(const uint8_t* vertices, size_t vertexCount, const uint8_t* indices, size_t indexCount);
    
    // Bind a texture for rendering
    void bindTexture(uint32_t textureId);
    
    // Set shader uniforms
    void setUniformMatrix4x4(const char* name, const float* matrix);
    void setUniformFloat(const char* name, float value);
    void setUniformVec4(const char* name, const float* vector);
}