#include "engine/VoiceMixer.h"
#include <cmath>

namespace sila::engine
{
// Small-speaker monitor constants (mirror app.js _SS_* and audio.py).
static constexpr double kSsFcSub   = 90.0;
static constexpr double kSsFcLow   = 150.0;
static constexpr double kSsDrive   = 2.5;
static constexpr double kSsBassGain = 0.8;
static constexpr float  kSsSoftKnee = 0.8f;

// 4-point cubic Hermite (Catmull-Rom) interpolation at fractional `pos` for the
// varispeed (pitch) read. Indices clamped to the buffer so the kernel never
// reads past either edge. Stateless — cheap enough for the per-voice hot path,
// and cleaner transients than linear without windowed-sinc's cost.
static float hermite4 (const float* src, int n, double pos)
{
    const int   i    = (int) std::floor (pos);
    const float frac = (float) (pos - i);
    auto at = [src, n] (int k) { return src[juce::jlimit (0, n - 1, k)]; };

    const float xm1 = at (i - 1), x0 = at (i), x1 = at (i + 1), x2 = at (i + 2);
    const float c0 = x0;
    const float c1 = 0.5f * (x1 - xm1);
    const float c2 = xm1 - 2.5f * x0 + 2.0f * x1 - 0.5f * x2;
    const float c3 = 0.5f * (x2 - xm1) + 1.5f * (x0 - x1);
    return ((c3 * frac + c2) * frac + c1) * frac + c0;
}

void VoiceMixer::prepare (double sr)
{
    sampleRate = sr;
    envAttack  = juce::jmax (1, (int) (0.001 * sr));   // ~1 ms note-on de-click
    envRelease = juce::jmax (1, (int) (0.008 * sr));   // ~8 ms note-off release
    reset();
}

void VoiceMixer::reset()
{
    voices.clear();
    ssSub[0] = ssSub[1] = ssLow[0] = ssLow[1] = ssHarm[0] = ssHarm[1] = 0.0;
}

void VoiceMixer::renderInto (juce::AudioBuffer<float>& block)
{
    const int n = block.getNumSamples();
    if (n <= 0) return;

    auto* L = block.getWritePointer (0);
    auto* R = block.getNumChannels() > 1 ? block.getWritePointer (1) : nullptr;

    for (size_t i = 0; i < voices.size();)
    {
        Voice& v = voices[i];

        // Defer voices whose start offset is beyond this block (port of the
        // delay_frames >= frames branch in audio.py::_callback).
        if (v.startOffset >= n)
        {
            v.startOffset -= n;
            ++i;
            continue;
        }

        const auto* src   = v.audio->getReadPointer (0);
        const int   srcN  = v.audio->getNumSamples();
        int j = v.startOffset;        // first output sample in this block
        v.startOffset = 0;

        const bool gated = v.gateSamples > 0;
        while (j < n && v.pos < (double) v.endPos)
        {
            // AR gate: min() of a rising attack ramp and a falling release ramp =
            // a trapezoid (a triangle if the gate is shorter than attack+release),
            // so edges are click-free at any length. One-shot voices skip release.
            float env = (v.elapsed < envAttack) ? (float) v.elapsed / (float) envAttack : 1.0f;
            if (gated)
            {
                const float rel = (float) (v.gateSamples + envRelease - v.elapsed) / (float) envRelease;
                env = juce::jmin (env, juce::jlimit (0.0f, 1.0f, rel));
                if (env <= 0.0f) break;   // gate fully closed
            }

            const float s = hermite4 (src, srcN, v.pos) * v.volume * env;
            L[j] += v.panL * s;
            if (R != nullptr) R[j] += v.panR * s;
            v.pos += v.rate;          // varispeed: rate = 2^(pitch_offset/12)
            ++v.elapsed;
            ++j;
        }

        if (v.pos >= (double) v.endPos || (gated && v.elapsed >= v.gateSamples + envRelease))
            voices.erase (voices.begin() + (long) i);   // sample ran out, or gate released
        else
            ++i;
    }
}

float VoiceMixer::softClip (float x, float knee)
{
    const float a = std::abs (x);
    if (a > knee)
    {
        const float span = 1.0f - knee;
        x = (x < 0 ? -1.0f : 1.0f) * (knee + span * std::tanh ((a - knee) / span));
    }
    return juce::jlimit (-1.0f, 1.0f, x);
}

void VoiceMixer::applyMaster (juce::AudioBuffer<float>& block, bool smallSpeaker, float masterVol)
{
    const int n = block.getNumSamples();
    const int chans = juce::jmin (2, block.getNumChannels());

    // Master gain.
    if (masterVol != 1.0f)
        block.applyGain (masterVol);

    if (! smallSpeaker)
    {
        for (int ch = 0; ch < block.getNumChannels(); ++ch)
        {
            auto* d = block.getWritePointer (ch);
            for (int i = 0; i < n; ++i)
                d[i] = juce::jlimit (-1.0f, 1.0f, d[i]);   // default: hard clip
        }
        return;
    }

    // Small-speaker monitor: drop deep sub, add audible bass harmonics, soft-limit.
    const double aSub = std::exp (-2.0 * juce::MathConstants<double>::pi * kSsFcSub / sampleRate);
    const double aLow = std::exp (-2.0 * juce::MathConstants<double>::pi * kSsFcLow / sampleRate);

    for (int ch = 0; ch < chans; ++ch)
    {
        auto* d = block.getWritePointer (ch);
        for (int i = 0; i < n; ++i)
        {
            const double x = d[i];
            ssSub[ch]  = aSub * ssSub[ch]  + (1.0 - aSub) * x;
            ssLow[ch]  = aLow * ssLow[ch]  + (1.0 - aLow) * x;
            const double harmRaw = std::tanh (kSsDrive * ssLow[ch]);
            ssHarm[ch] = aLow * ssHarm[ch] + (1.0 - aLow) * harmRaw;
            const double harm = harmRaw - ssHarm[ch];          // high-passed harmonics
            const double y = (x - ssSub[ch]) + kSsBassGain * harm;
            d[i] = softClip ((float) y, kSsSoftKnee);
        }
    }
}
} // namespace sila::engine
