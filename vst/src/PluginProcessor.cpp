#include "PluginProcessor.h"
#include "PluginEditor.h"

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
    // Automatable globals. Per-step / per-track structured data lives in state.
    layout.add (std::make_unique<AudioParameterFloat>(
        ParameterID { "masterVol", 1 }, "Master Volume", 0.0f, 2.0f, 1.0f));
    layout.add (std::make_unique<AudioParameterFloat>(
        ParameterID { "swing", 1 }, "Swing", 0.0f, 1.0f, 0.0f));
    layout.add (std::make_unique<AudioParameterBool>(
        ParameterID { "smallSpeaker", 1 }, "Small-Speaker Monitor", false));
    return layout;
}

void SilaAudioProcessor::prepareToPlay (double sr, int /*samplesPerBlock*/)
{
    sampleRate = sr;
    lastPpq = -1.0;
    // TODO: sampler.prepare(sr); mixer.prepare(sr);  (engine ports)
}

void SilaAudioProcessor::processBlock (juce::AudioBuffer<float>& buffer,
                                       juce::MidiBuffer& /*midi*/)
{
    juce::ScopedNoDenormals noDenormals;
    buffer.clear();

    // --- Host transport drives the sequencer (replaces PlaybackClock) ---------
    if (auto* ph = getPlayHead())
    {
        if (auto pos = ph->getPosition())
        {
            if (pos->getIsPlaying())
                scheduleAndRender (buffer, *pos);
        }
    }

    // TODO: mixer.renderActiveVoices(buffer);   // port of audio.py::_callback
    //       mixer.applyMaster(buffer, smallSpeaker, masterVol);  // soft-clip etc.
}

void SilaAudioProcessor::scheduleAndRender (juce::AudioBuffer<float>& buffer,
                                            const juce::AudioPlayHead::PositionInfo& pos)
{
    // Pull-based equivalent of clock.py::_run:
    //   1. ppq + bpm + sampleRate → position of each 16th note in samples.
    //   2. For every 16th boundary inside [0, numSamples): sequencer.tick(),
    //      spawn voices at that sample offset (replaces delay_frames); apply
    //      swing + micro-timing as offset adjustments.
    //   3. Song-mode pattern swap on bar boundaries, keyed off ppq.
    //
    // Sketch (commented until the engine is ported):
    //
    // const double bpm        = pos.getBpm().orFallback (120.0);
    // const double ppq        = pos.getPpqPosition().orFallback (0.0);
    // const double sixteenth  = 0.25;                       // 1/16 note in quarters
    // const double samplesPer16 = (60.0 / bpm / 4.0) * sampleRate;
    // ... find boundaries, call sequencer.tick(), mixer.addVoice(audio, offset) ...

    juce::ignoreUnused (buffer, pos);
}

void SilaAudioProcessor::getStateInformation (juce::MemoryBlock& dest)
{
    // Params + the project JSON blob (tracks/steps/patterns/song chain),
    // mirroring models/project.py. Sketch:
    //   auto state = apvts.copyState();
    //   state.setProperty ("projectJson", buildProjectJson(), nullptr);
    //   copyXmlToBinary (*state.createXml(), dest);
    juce::ignoreUnused (dest);
}

void SilaAudioProcessor::setStateInformation (const void* data, int sizeInBytes)
{
    // Inverse of getStateInformation; rebuild the engine model from JSON.
    juce::ignoreUnused (data, sizeInBytes);
}

juce::AudioProcessorEditor* SilaAudioProcessor::createEditor()
{
    return new SilaAudioProcessorEditor (*this);
}

// The DAW entry point.
juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new SilaAudioProcessor();
}
