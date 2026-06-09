#include "PluginProcessor.h"
#include <cmath>

SilaAudioProcessor::SilaAudioProcessor()
    : juce::AudioProcessor (BusesProperties()
          .withOutput ("Output", juce::AudioChannelSet::stereo(), true)),
      apvts (*this, nullptr, "SILA", makeParameters())
{
}

SilaAudioProcessor::~SilaAudioProcessor() = default;

juce::AudioProcessorValueTreeState::ParameterLayout SilaAudioProcessor::makeParameters()
{
    using namespace juce;
    AudioProcessorValueTreeState::ParameterLayout layout;
    layout.add (std::make_unique<AudioParameterFloat>(
        ParameterID { "masterVol", 1 }, "Master Volume", 0.0f, 2.0f, 1.0f));
    layout.add (std::make_unique<AudioParameterFloat>(
        ParameterID { "swing", 1 }, "Swing", 0.0f, 1.0f, 0.0f));
    layout.add (std::make_unique<AudioParameterBool>(
        ParameterID { "smallSpeaker", 1 }, "Small-Speaker Monitor", false));
    return layout;
}

// A short percussive kick: pitch-dropping sine with an exponential envelope.
juce::AudioBuffer<float> SilaAudioProcessor::makeKick (double sr)
{
    const double dur = 0.18;
    const int n = juce::jmax (1, (int) (sr * dur));
    juce::AudioBuffer<float> b (1, n);
    auto* d = b.getWritePointer (0);
    double phase = 0.0;
    for (int i = 0; i < n; ++i)
    {
        const double t = i / sr;
        const double f = 45.0 + 120.0 * std::exp (-t * 30.0);   // 165 Hz → 45 Hz
        phase += 2.0 * juce::MathConstants<double>::pi * f / sr;
        const double env = std::exp (-t * 12.0);
        d[i] = (float) (std::sin (phase) * env * 0.9);
    }
    return b;
}

void SilaAudioProcessor::prepareToPlay (double sr, int /*samplesPerBlock*/)
{
    sampleRate = sr;
    internalPpq = 0.0;
    lastFiredSixteenth = -1;

    // Phase 2: one track, a kick on every beat (4-on-the-floor).
    for (int i = 0; i < 16; ++i)
        pattern[i] = (i % 4) == 0;

    sampler.prepare (sr);
    sampler.clear();
    sampler.addBuffer (makeKick (sr));   // synthesized so it sounds with no UI/file

    mixer.prepare (sr);
}

void SilaAudioProcessor::processBlock (juce::AudioBuffer<float>& buffer,
                                       juce::MidiBuffer& /*midi*/)
{
    juce::ScopedNoDenormals noDenormals;
    buffer.clear();

    const int numSamples = buffer.getNumSamples();

    // --- Resolve transport: host if playing, else internal (Standalone only) --
    double bpm = kDefaultBpm, ppqStart = internalPpq;
    bool playing = false;

    if (auto* ph = getPlayHead())
    {
        if (auto pos = ph->getPosition())
        {
            if (pos->getIsPlaying())
            {
                playing  = true;
                bpm      = pos->getBpm().orFallback (kDefaultBpm);
                ppqStart = pos->getPpqPosition().orFallback (internalPpq);
            }
        }
    }

    if (! playing && wrapperType == wrapperType_Standalone)
    {
        playing  = true;            // free-run so the Standalone app makes sound
        bpm      = kDefaultBpm;
        ppqStart = internalPpq;
    }

    if (playing && bpm > 0.0)
    {
        scheduleTriggers (ppqStart, bpm, numSamples);
        // Advance the internal clock to the end of this block (keeps it in sync
        // with the host when host-driven; carries the free-run when not).
        const double blockQuarters = numSamples * (bpm / 60.0) / sampleRate;
        internalPpq = ppqStart + blockQuarters;
    }

    // Tails keep ringing even when stopped, so always render + master.
    mixer.renderInto (buffer);

    const float masterVol   = apvts.getRawParameterValue ("masterVol")->load();
    const bool  smallSpeaker = apvts.getRawParameterValue ("smallSpeaker")->load() > 0.5f;
    mixer.applyMaster (buffer, smallSpeaker, masterVol);
}

void SilaAudioProcessor::scheduleTriggers (double ppqStart, double bpm, int numSamples)
{
    // 1 quarter note = 4 sixteenths; ppq is in quarter notes.
    const double sixteenthStart = ppqStart * 4.0;
    const double samplesPer16   = sampleRate * 60.0 / bpm / 4.0;

    // If the transport jumped backwards (loop/relocate), re-arm.
    if (sixteenthStart + 1e-6 < (double) lastFiredSixteenth)
        lastFiredSixteenth = (long) std::floor (sixteenthStart) - 1;

    long idx = (long) std::ceil (sixteenthStart - 1e-9);
    for (;;)
    {
        const double offset = (idx - sixteenthStart) * samplesPer16;
        if (offset >= numSamples)
            break;
        if (offset >= 0.0 && idx > lastFiredSixteenth)
        {
            lastFiredSixteenth = idx;
            const int step = (int) (((idx % 16) + 16) % 16);
            if (pattern[step])
            {
                const auto slice = sampler.get (100);
                if (slice.buffer != nullptr)
                {
                    sila::engine::Voice v;
                    v.audio       = slice.buffer;
                    v.pos         = slice.start;
                    v.endPos      = slice.start + slice.length;
                    v.startOffset = (int) offset;     // sample-accurate within the block
                    v.volume      = 1.0f;
                    v.panL = v.panR = 0.70710678f;     // centre
                    mixer.addVoice (v);
                }
            }
        }
        ++idx;
    }
}

void SilaAudioProcessor::getStateInformation (juce::MemoryBlock& dest)
{
    if (auto xml = apvts.copyState().createXml())
        copyXmlToBinary (*xml, dest);
}

void SilaAudioProcessor::setStateInformation (const void* data, int sizeInBytes)
{
    if (auto xml = getXmlFromBinary (data, sizeInBytes))
        apvts.replaceState (juce::ValueTree::fromXml (*xml));
}

juce::AudioProcessorEditor* SilaAudioProcessor::createEditor()
{
    // Phase 2: generic editor shows the parameters and is guaranteed to build.
    // The WebView editor (src/PluginEditor.*) comes online in Phase 4.
    return new juce::GenericAudioProcessorEditor (*this);
}

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new SilaAudioProcessor();
}
