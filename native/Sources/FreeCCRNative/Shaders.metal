// Draws the current preview texture into an arbitrary quad — position and
// size (in NDC) are computed on the CPU by CanvasTransform and handed in as
// a uniform, so pan/zoom is "where we draw the quad", not a shader concern.
// The color-science compute kernel is Phase 4's job, not this one.
#include <metal_stdlib>
using namespace metal;

struct QuadUniforms {
    float2 origin; // NDC position of the quad's top-left corner
    float2 size;   // NDC width/height (size.y is negative: NDC y+ is up,
                    // but our quad's local y+ goes down the image)
};

struct VertexOut {
    float4 position [[position]];
    float2 uv;
};

vertex VertexOut vertex_main(uint vertexID [[vertex_id]],
                              constant QuadUniforms &u [[buffer(0)]]) {
    // Local unit-quad corners, y+ downward (top-left origin), ordered for a
    // triangle strip: bottom-left, bottom-right, top-left, top-right.
    float2 corners[4] = { float2(0, 1), float2(1, 1), float2(0, 0), float2(1, 0) };
    float2 uvs[4] = { float2(0, 1), float2(1, 1), float2(0, 0), float2(1, 0) };

    VertexOut out;
    out.position = float4(u.origin + corners[vertexID] * u.size, 0.0, 1.0);
    out.uv = uvs[vertexID];
    return out;
}

fragment float4 fragment_main(VertexOut in [[stage_in]],
                               texture2d<float> tex [[texture(0)]]) {
    constexpr sampler s(mag_filter::linear, min_filter::linear);
    return tex.sample(s, in.uv);
}
