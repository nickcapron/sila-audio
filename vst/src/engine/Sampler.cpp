#include "engine/Sampler.h"
#include "engine/Resample.h"

namespace sila::engine
{
bool Sampler::addFile (const juce::File& file, int velMin, int velMax, int rrGroup,
                       float start, float end)
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

    // Resample to the device rate so the file plays at its original pitch/speed
    // regardless of its own rate (offline windowed-sinc; the audio thread still
    // reads the result 1:1). Files already at the device rate pass through.
    mono = resampleMonoTo (mono, reader->sampleRate, sampleRate);

    addBuffer (std::move (mono), velMin, velMax, rrGroup, start, end);
    return true;
}

void Sampler::addBuffer (juce::AudioBuffer<float> mono, int velMin, int velMax, int rrGroup,
                         float start, float end)
{
    SampleLayer layer;
    layer.audio   = std::move (mono);
    layer.velMin  = velMin;
    layer.velMax  = velMax;
    layer.rrGroup = rrGroup;
    layer.start   = start;
    layer.end     = end;
    layers.push_back (std::move (layer));
}

SampleSlice Sampler::get (int velocity, float startOverride, float endOverride)
{
    // AUDIO THREAD — must not allocate. Two passes over the layers (count, then
    // pick) instead of building a candidates vector; the rr counter is a fixed
    // array, not a map. Selects the round-robin layer among those whose velocity
    // range contains the trig velocity. Mirrors sampler.py.
    auto matches = [&] (const SampleLayer& l) { return velocity >= l.velMin && velocity <= l.velMax; };

    int count = 0, firstMatch = -1;
    for (int i = 0; i < (int) layers.size(); ++i)
        if (matches (layers[(size_t) i]))
        {
            if (firstMatch < 0) firstMatch = i;
            ++count;
        }
    if (count == 0)
        return {};

    // Round-robin within the first match's group (same advance as before; the
    // stored counter stays bounded by `count`, so no overflow).
    const int group = ((layers[(size_t) firstMatch].rrGroup % kMaxRrGroups) + kMaxRrGroups) % kMaxRrGroups;
    const int pick  = rrCounters[(size_t) group] % count;
    rrCounters[(size_t) group] = pick + 1;

    // Second pass: the pick-th matching layer.
    int chosen = firstMatch, seen = 0;
    for (int i = firstMatch; i < (int) layers.size(); ++i)
        if (matches (layers[(size_t) i]))
        {
            if (seen == pick) { chosen = i; break; }
            ++seen;
        }

    const SampleLayer& layer = layers[(size_t) chosen];
    const int n = layer.audio.getNumSamples();

    const float sFrac = (startOverride >= 0.0f) ? startOverride : layer.start;
    const float eFrac = (endOverride   >= 0.0f) ? endOverride   : layer.end;

    int start = juce::jlimit (0, n - 1, (int) (sFrac * n));
    int end   = juce::jlimit (start + 1, n, (int) (eFrac * n));

    return { &layer.audio, start, end - start };
}

std::vector<float> Sampler::computePeaks (int points) const
{
    if (layers.empty() || points <= 0)
        return {};

    const juce::AudioBuffer<float>& audio = layers.front().audio;
    const int n = audio.getNumSamples();
    if (n <= 0)
        return {};

    const float* data = audio.getReadPointer (0);
    std::vector<float> peaks ((size_t) points, 0.0f);
    for (int b = 0; b < points; ++b)
    {
        const int from = (int) ((juce::int64) b       * n / points);
        const int to   = (int) ((juce::int64) (b + 1) * n / points);
        float peak = 0.0f;
        for (int i = from; i < to; ++i)
            peak = juce::jmax (peak, std::abs (data[i]));
        peaks[(size_t) b] = juce::jmin (1.0f, peak);
    }
    return peaks;
}
} // namespace sila::engine
