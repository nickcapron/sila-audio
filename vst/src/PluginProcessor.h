#pragma once

#include <juce_audio_processors/juce_audio_processors.h>
#include "engine/Sampler.h"
#include "engine/VoiceMixer.h"

// SILA plugin processor.
//
// Phase 2: a single track triggers a kick on a 4-on-the-floor pattern, synced
// to the transport. A real DAW's transport governs; in the Standalone wrapper
// (no host transport) an internal free-running clock engages so it plays.
//
// The full Sequencer (per-track patterns, trig conditions, swing, song mode)
// arrives in Phase 3 — for now scheduleTriggers() uses a hard-coded pattern to
// prove the host-synced timing + Sampler + VoiceMixer path end to end.
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
    double getTailLengthSeconds() const override { return 0.5; }

    int getNumPrograms() override { return 1; }
    int getCurrentProgram() override { return 0; }
    void setCurrentProgram (int) override {}
    const juce::String getProgramName (int) override { return {}; }
    void changeProgramName (int, const juce::String&) override {}

    void getStateInformation (juce::MemoryBlock&) override;
    void setStateInformation (const void*, int sizeInBytes) override;

    juce::AudioProcessorValueTreeState apvts;

private:
    static juce::AudioProcessorValueTreeState::ParameterLayout makeParameters();

    // Find the 16th-note boundaries inside this block and trigger the pattern
    // sample-accurately. Port of clock.py::_run timing, pull-based.
    void scheduleTriggers (double ppqStart, double bpm, int numSamples);

    static juce::AudioBuffer<float> makeKick (double sampleRate);

    static constexpr double kDefaultBpm = 120.0;   // standalone free-run tempo

    double sampleRate { 48000.0 };
    double internalPpq { 0.0 };       // free-running clock for the Standalone case
    long   lastFiredSixteenth { -1 }; // dedupe boundaries across blocks
    bool   pattern[16] {};            // Phase 2: hard-coded 4-on-the-floor

    sila::engine::Sampler    sampler;   // ../sila/engine/sampler.py
    sila::engine::VoiceMixer mixer;     // ../sila/engine/audio.py

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (SilaAudioProcessor)
};
