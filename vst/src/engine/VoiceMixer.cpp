#include "engine/VoiceMixer.h"
#include "engine/Project.h"   // LfoShape / LfoDest enums (used by the LFO update)
#include <cmath>

namespace sila::engine
{
// Small-speaker monitor constants (mirror app.js _SS_* and audio.py).
static constexpr double kSsFcSub   = 90.0;
static constexpr double kSsFcLow   = 150.0;
static constexpr double kSsDrive   = 2.5;
static constexpr double kSsBassGain = 0.8;
static constexpr float  kSsSoftKnee = 0.8f;

// LFO control-rate block: the shape is re-evaluated and its destination retargeted
// once every this-many samples (the expensive cutoff coeff re-bake runs at ~sr/32,
// not per sample). Used by both the render loop and the phase advance, so they
// can't drift out of step.
static constexpr int kLfoControlBlock = 32;

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

// Bake TPT-SVF coeffs from cutoff/resonance. Shared by the trigger bake
// (PluginProcessor) and the LFO control-rate update. (process() is inline in the
// header — only the per-sample integrator runs there.)
void TptSvf::bake (float cutoff, float resonance, double sampleRate)
{
    const double fc = juce::jlimit (20.0, sampleRate * 0.49,
                                    20.0 * std::pow (1000.0, (double) cutoff));
    const double Q   = 0.5 + (double) resonance * 19.5;     // matches fx.py
    const double kk  = 1.0 / Q;
    const double g   = std::tan (juce::MathConstants<double>::pi * fc / sampleRate);
    const double aa1 = 1.0 / (1.0 + g * (g + kk));
    a1 = (float) aa1;
    a2 = (float) (g * aa1);
    a3 = (float) (g * g * aa1);    // a3 = g*a2
    k  = (float) kk;
}

// LFO shape in [-1,1] for the current phase (sine/tri/square/saw match lfo.py;
// random returns the held sample-and-hold value).
static float lfoEvalShape (const Voice::LFO& lfo)
{
    const double ph = lfo.phase;
    constexpr double PI = juce::MathConstants<double>::pi;
    switch ((LfoShape) lfo.shape)
    {
        case LfoShape::Sine:     return (float) std::sin (ph);
        case LfoShape::Triangle: return (float) (2.0 * std::abs (std::fmod (ph / PI, 2.0) - 1.0) - 1.0);
        case LfoShape::Square:   return std::sin (ph) >= 0.0 ? 1.0f : -1.0f;
        case LfoShape::Sawtooth: return (float) (std::fmod (ph / PI, 2.0) - 1.0);
        case LfoShape::Random:   return lfo.shVal;
    }
    return 0.0f;
}

void VoiceMixer::updateVoiceLfo (Voice& v)
{
    const float val = lfoEvalShape (v.lfo) * v.lfo.depth;   // [-depth, depth]
    switch ((LfoDest) v.lfo.dest)
    {
        case LfoDest::Cutoff:
            v.svf.bake (juce::jlimit (0.0f, 1.0f, v.baseCutoff + val), v.baseResonance, sampleRate);
            break;
        case LfoDest::Volume:                                       // tremolo
            v.volume = juce::jlimit (0.0f, 1.0f, v.baseGain * (1.0f + val));
            break;
        case LfoDest::Pitch:                                        // vibrato (±depth octave)
            v.rate = v.baseRate * std::pow (2.0, (double) val);
            break;
    }

    // Advance one control block; a new sample-and-hold value at each cycle wrap.
    const double twoPi = 2.0 * juce::MathConstants<double>::pi;
    v.lfo.phase += (double) kLfoControlBlock * v.lfo.inc;
    bool wrapped = false;
    while (v.lfo.phase >= twoPi) { v.lfo.phase -= twoPi; wrapped = true; }
    if (wrapped)
        v.lfo.shVal = rng.nextFloat() * 2.0f - 1.0f;
}

void VoiceMixer::prepare (double sr)
{
    sampleRate = sr;
    envAttack  = juce::jmax (1, (int) (0.001 * sr));   // ~1 ms note-on de-click
    envRelease = juce::jmax (1, (int) (0.008 * sr));   // ~8 ms note-off release
    reset();
    // Pre-grow the voice pool so addVoice()'s push_back never reallocates on the
    // audio thread in steady state. reset()/clear() preserve this capacity. Song
    // mode's denser triggering makes the headroom worthwhile; a burst past it
    // still amortizes correctly (the cap is generous, not hard).
    voices.reserve (kMaxVoices);
}

void VoiceMixer::reset()
{
    voices.clear();
    ssSub[0] = ssSub[1] = ssLow[0] = ssLow[1] = ssHarm[0] = ssHarm[1] = 0.0;
}

void VoiceMixer::renderInto (juce::AudioBuffer<float>& block, const std::vector<TrackMix>& trackMix)
{
    const int n = block.getNumSamples();
    if (n <= 0) return;

    auto* L = block.getWritePointer (0);
    auto* R = block.getNumChannels() > 1 ? block.getWritePointer (1) : nullptr;

    static const TrackMix unity {};   // fallback if a voice's track index is stale

    for (size_t i = 0; i < voices.size();)
    {
        Voice& v = voices[i];
        const TrackMix& tm = (v.trackIndex >= 0 && v.trackIndex < (int) trackMix.size())
                                 ? trackMix[(size_t) v.trackIndex] : unity;

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
            // Control-rate LFO: re-evaluate + retarget every 32 samples (the
            // expensive cutoff coeff recompute runs at ~1.5 kHz, not per sample).
            if (v.lfo.on)
            {
                if (v.lfo.ctr == 0) { updateVoiceLfo (v); v.lfo.ctr = kLfoControlBlock; }
                --v.lfo.ctr;
            }

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

            float x = hermite4 (src, srcN, v.pos);
            if (v.filterOn) x = v.svf.process (x);   // filter -> envelope -> gain/pan
            const float s = x * v.volume * env * tm.gain;
            L[j] += tm.panL * s;
            if (R != nullptr) R[j] += tm.panR * s;
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
