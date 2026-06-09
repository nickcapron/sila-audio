#pragma once
#include <juce_audio_formats/juce_audio_formats.h>
#include <vector>

// Port of ../../sila/engine/sampler.py.
// Per-track: velocity layers + round-robin selection, start/end slicing.
// load() decodes via juce::AudioFormatManager instead of audio_loader.py.
namespace sila::engine
{
class Sampler
{
public:
    void prepare (double sampleRate);

    // Velocity-layer + round-robin pick, returns the slice to play (or nullptr).
    // Mirrors SamplePlayer.get() / get_with_offset().
    const juce::AudioBuffer<float>* get (int velocity,
                                         float startOverride = -1.0f,
                                         float endOverride   = -1.0f);

    // TODO: load layers (path, velMin/Max, start/end, rrGroup) from the model.
    void loadLayers (/* const std::vector<SampleLayer>& */);

private:
    juce::AudioFormatManager formats;   // formats.registerBasicFormats() in ctor
    // std::vector<LoadedSample> layers;
    // std::map<int,int> rrCounters;
};
} // namespace sila::engine
