#pragma once
#include <juce_audio_basics/juce_audio_basics.h>
#include <cmath>

// Shared offline resampler — highest-quality windowed-sinc anti-aliasing, paid
// once on the message thread. Used by the Sampler (file -> device rate, so
// samples play at their original pitch) and by the Digitakt export (source ->
// 48 kHz). Mono in, mono out. A no-op rate (|src-dst| < 1e-6) returns a copy.
namespace sila::engine
{
inline juce::AudioBuffer<float> resampleMonoTo (const juce::AudioBuffer<float>& mono,
                                                double srcRate, double dstRate)
{
    const int len = mono.getNumSamples();
    if (len <= 0 || srcRate <= 0.0 || dstRate <= 0.0 || std::abs (srcRate - dstRate) < 1.0e-6)
        return mono;   // already at target (or empty) — exact passthrough copy

    const double ratio  = srcRate / dstRate;                  // input samples per output
    const int    outLen = (int) std::floor ((double) len / ratio);   // floor => never reads past `len`
    if (outLen <= 0)
        return mono;

    juce::AudioBuffer<float> out (1, outLen);
    juce::WindowedSincInterpolator interp;
    interp.reset();
    interp.process (ratio, mono.getReadPointer (0), out.getWritePointer (0), outLen);
    return out;
}
} // namespace sila::engine
