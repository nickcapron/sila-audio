#include "engine/Sampler.h"

namespace sila::engine
{
bool Sampler::addFile (const juce::File& file, int velMin, int velMax, int rrGroup)
{
    std::unique_ptr<juce::AudioFormatReader> reader (formats.createReaderFor (file));
    if (reader == nullptr)
        return false;

    const int len = (int) reader->lengthInSamples;
    if (len <= 0)
        return false;

    juce::AudioBuffer<float> src ((int) reader->numChannels, len);
    reader->read (&src, 0, len, 0, true, true);

    // Downmix to mono (load_audio_mono_f32 equivalent).
    juce::AudioBuffer<float> mono (1, len);
    mono.clear();
    auto* dst = mono.getWritePointer (0);
    for (int ch = 0; ch < src.getNumChannels(); ++ch)
    {
        const auto* s = src.getReadPointer (ch);
        for (int i = 0; i < len; ++i)
            dst[i] += s[i];
    }
    if (src.getNumChannels() > 1)
        mono.applyGain (1.0f / (float) src.getNumChannels());

    addBuffer (std::move (mono), velMin, velMax, rrGroup);
    return true;
}

void Sampler::addBuffer (juce::AudioBuffer<float> mono, int velMin, int velMax, int rrGroup)
{
    SampleLayer layer;
    layer.audio   = std::move (mono);
    layer.velMin  = velMin;
    layer.velMax  = velMax;
    layer.rrGroup = rrGroup;
    layers.push_back (std::move (layer));
}

SampleSlice Sampler::get (int velocity, float startOverride, float endOverride)
{
    // Candidates whose velocity range contains the trig velocity.
    std::vector<int> candidates;
    for (int i = 0; i < (int) layers.size(); ++i)
        if (velocity >= layers[i].velMin && velocity <= layers[i].velMax)
            candidates.push_back (i);

    if (candidates.empty())
        return {};

    // Round-robin within the first candidate's group (matches sampler.py).
    const int group = layers[candidates.front()].rrGroup;
    const int idx   = rrCounters[group] % (int) candidates.size();
    rrCounters[group] = idx + 1;

    const SampleLayer& layer = layers[candidates[idx]];
    const int n = layer.audio.getNumSamples();

    const float sFrac = (startOverride >= 0.0f) ? startOverride : layer.start;
    const float eFrac = (endOverride   >= 0.0f) ? endOverride   : layer.end;

    int start = juce::jlimit (0, n - 1, (int) (sFrac * n));
    int end   = juce::jlimit (start + 1, n, (int) (eFrac * n));

    return { &layer.audio, start, end - start };
}
} // namespace sila::engine
