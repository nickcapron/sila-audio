#pragma once

#include <juce_audio_processors/juce_audio_processors.h>

// SILA plugin processor (scaffold).
//
// Replaces the standalone app's PlaybackClock + AudioEngine: instead of a
// wall-clock sleep loop driving sounddevice, the host calls processBlock() and
// we render the sequencer sample-accurately against the host transport.
//
// Engine modules (Sequencer/Sampler/VoiceMixer/Fx) are declared in src/engine
// and ported from ../sila/engine per DESIGN.md. They are forward-declared here
// to keep the scaffold compiling before they exist.
class SilaAudioProcessor : public juce::AudioProcessor
{
public:
    SilaAudioProcessor();
    ~SilaAudioProcessor() override;

    void prepareToPlay (double sampleRate, int samplesPerBlock) override;
    void releaseResources() override {}
    void processBlock (juce::AudioBuffer<float>&, juce::MidiBuffer&) override;

    juce::AudioProcessorEditor* createEditor() override;
    bool hasEditor() const override { return true; }

    const juce::String getName() const override { return "SILA"; }
    bool acceptsMidi()  const override { return true;  }
    bool producesMidi() const override { return false; }
    bool isMidiEffect()  const override { return false; }
    double getTailLengthSeconds() const override { return 0.0; }

    int getNumPrograms() override { return 1; }
    int getCurrentProgram() override { return 0; }
    void setCurrentProgram (int) override {}
    const juce::String getProgramName (int) override { return {}; }
    void changeProgramName (int, const juce::String&) override {}

    // Whole-project state (tracks/steps/patterns/song chain) as a JSON blob,
    // plus the automatable params. Mirrors models/project.py serialization.
    void getStateInformation (juce::MemoryBlock&) override;
    void setStateInformation (const void*, int sizeInBytes) override;

    // Automatable parameters (master volume, swing, small-speaker monitor, …).
    juce::AudioProcessorValueTreeState apvts;

private:
    static juce::AudioProcessorValueTreeState::ParameterLayout makeParameters();

    // Convert host PPQ position to 16th-note scheduling within this block, then
    // tick the sequencer at each boundary. Port of clock.py::_run timing.
    void scheduleAndRender (juce::AudioBuffer<float>&, const juce::AudioPlayHead::PositionInfo&);

    double sampleRate { 48000.0 };
    double lastPpq    { -1.0 };   // for detecting 16th-note boundaries across blocks

    // engine::Sequencer  sequencer;   // ../sila/engine/sequencer.py
    // engine::Sampler     sampler;     // ../sila/engine/sampler.py
    // engine::VoiceMixer  mixer;       // ../sila/engine/audio.py (voices + master)

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (SilaAudioProcessor)
};
