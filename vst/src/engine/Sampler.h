#pragma once
#include <juce_audio_formats/juce_audio_formats.h>
#include <vector>
#include <map>

// Port of ../../sila/engine/sampler.py.
// Per-track: velocity layers + round-robin selection, start/end slicing.
// Buffers are stored mono (downmixed on load), matching load_audio_mono_f32.
namespace sila::engine
{
// A region of a layer buffer to play (no copy — points into the layer).
struct SampleSlice
{
    const juce::AudioBuffer<float>* buffer = nullptr;  // null = no match
    int start  = 0;
    int length = 0;
};

struct SampleLayer
{
    juce::AudioBuffer<float> audio;     // mono
    int   velMin = 0, velMax = 127;
    float start = 0.0f, end = 1.0f;     // 0..1 fractions of the buffer
    int   rrGroup = 0;
};

class Sampler
{
public:
    Sampler() { formats.registerBasicFormats(); }

    void prepare (double sr) { sampleRate = sr; }
    void clear() { layers.clear(); rrCounters.clear(); }

    // Decode a WAV/AIFF file to a mono layer. (Phase 2: no sample-rate
    // conversion yet — files not at the host rate play at the wrong pitch.)
    bool addFile (const juce::File&, int velMin = 0, int velMax = 127, int rrGroup = 0);

    // Add an in-memory mono buffer as a layer (e.g. a synthesized test sample).
    void addBuffer (juce::AudioBuffer<float> mono, int velMin = 0, int velMax = 127, int rrGroup = 0);

    // Velocity-layer select + round-robin within the group; returns the slice
    // to play. Mirrors SamplePlayer.get() / get_with_offset().
    SampleSlice get (int velocity, float startOverride = -1.0f, float endOverride = -1.0f);

private:
    juce::AudioFormatManager formats;
    std::vector<SampleLayer> layers;
    std::map<int, int> rrCounters;     // rrGroup -> counter
    double sampleRate { 48000.0 };
};
} // namespace sila::engine
