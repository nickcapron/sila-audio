#pragma once

#include <juce_audio_processors/juce_audio_processors.h>
#include "engine/Sampler.h"
#include "engine/VoiceMixer.h"
#include "engine/Sequencer.h"
#include <vector>
#include <memory>
#include <atomic>

// SILA plugin processor.
//
// Phase 3: a real Sequencer drives per-track patterns (trig conditions,
// probability, mute/solo) synced to the transport, with swing + micro-timing
// applied as sample-accurate offsets. A DAW's transport governs; in the
// Standalone wrapper (no host transport) an internal free-running clock engages
// so it plays. Until the Phase 4 UI bridge exists, prepareToPlay() builds a
// small in-code demo project so the features are audible in the Standalone.
//
// Phase 3b adds song mode: a chain of pattern slots played one bar each, with
// the active slot DERIVED from the transport position (no audio-thread mutation
// or allocation). FX/LFO/pitch and preset state come in Phase 5.
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

    // Latest transport position (quarter notes), published by processBlock for
    // the editor to read on the message thread (lock-free; C++ -> UI playhead).
    std::atomic<double> currentPpq { 0.0 };

    // Transport status, published by processBlock for the editor to read on the
    // message thread (port of /sequencer/status). The editor pushes these to the
    // UI as a "status" event on change, replacing the Python app's 2 s poll.
    std::atomic<bool>   transportPlaying { false };
    std::atomic<double> currentBpm       { kDefaultBpm };
    std::atomic<int>    currentSongSlot  { -1 };   // -1 = song mode off / not playing

    // ── RCU concurrency seam (DESIGN.md) ───────────────────────────────────
    // The structural project state is an immutable snapshot. The audio thread
    // does ONE atomic load per block and only reads it. The message thread (UI
    // edits) publishes a new snapshot via editProject(); superseded snapshots
    // go on retiredProjects and are freed by reapRetired() on the message
    // thread, so the audio thread never runs a delete.
    using ProjectPtr = std::shared_ptr<const sila::engine::Project>;

    ProjectPtr snapshot() const { return liveProject.load (std::memory_order_acquire); }

    // Apply an edit on the message thread: copy the current snapshot, mutate the
    // copy, publish it atomically, retire the old one. Returns the new snapshot.
    template <typename Mutator>
    ProjectPtr editProject (Mutator&& mutate)
    {
        auto next = std::make_shared<sila::engine::Project> (*liveProject.load());
        mutate (*next);
        ProjectPtr published = next;
        auto old = liveProject.exchange (published, std::memory_order_acq_rel);
        if (old) retiredProjects.push_back (std::move (old));
        return published;
    }

    // Free retired snapshots no reader still holds. Message thread only.
    void reapRetired();

    // ── Sampler bank (RCU, parallel to the Project) ────────────────────────
    // The audio thread indexes a sampler per track. Files are loaded on the
    // message thread, so the bank is published the same way as the Project: an
    // immutable vector of shared samplers, swapped atomically and retired on the
    // message thread. Sampler::get() mutates round-robin state, but only the
    // audio thread ever calls it, so a shared (non-const) Sampler is safe.
    using SamplerBank    = std::vector<std::shared_ptr<sila::engine::Sampler>>;
    using SamplerBankPtr = std::shared_ptr<const SamplerBank>;

    SamplerBankPtr samplerSnapshot() const { return liveSamplers.load (std::memory_order_acquire); }

    // Message thread: build a fresh sampler for one track from its sample layers
    // and publish a new bank (other tracks reuse their existing sampler + RR
    // state). No-op if the index is out of range or the bank is unset.
    void assignTrackSamples (int trackIndex, const std::vector<sila::engine::SampleRef>& layers);

    // ~/SILA/library — root for resolving relative sample paths from the browser.
    static juce::File libraryRoot();

private:
    static juce::AudioProcessorValueTreeState::ParameterLayout makeParameters();

    // Find the 16th-note boundaries inside this block, evaluate the Sequencer at
    // each, and spawn voices sample-accurately (swing + micro-timing folded into
    // the sample offset). Reads the immutable snapshot + live performance
    // scalars. Port of clock.py::_run timing, pull-based.
    void scheduleTriggers (const sila::engine::Project& project,
                           double ppqStart, double bpm, int numSamples,
                           float swing, bool songMode, bool fillActive);

    // Build the in-code demo project; returns the snapshot and (re)builds the
    // parallel sampler array. Replaced by UI-authored state in later steps.
    ProjectPtr buildDemoProject (double sampleRate);

    static juce::AudioBuffer<float> makeKick  (double sampleRate);
    static juce::AudioBuffer<float> makeSnare (double sampleRate);
    static juce::AudioBuffer<float> makeHat   (double sampleRate);

    static constexpr double kDefaultBpm = 120.0;   // standalone free-run tempo

    double sampleRate { 48000.0 };
    double internalPpq { 0.0 };       // free-running clock for the Standalone case
    long   lastFiredSixteenth { -1 }; // dedupe boundaries across blocks

    // Live immutable project snapshot (RCU). Audio thread loads it per block.
    std::atomic<ProjectPtr> liveProject;
    // Superseded snapshots awaiting reclamation on the message thread.
    std::vector<ProjectPtr> retiredProjects;

    // Performance scalar not (yet) an APVTS param; read by the audio thread.
    std::atomic<bool> fillActive { false };

    sila::engine::Sequencer  sequencer; // ../sila/engine/sequencer.py (stateless)

    // Live sampler bank (RCU). Audio thread loads it once per block; the message
    // thread swaps a track's sampler on assignment. Parallel to snapshot tracks.
    std::atomic<SamplerBankPtr> liveSamplers;
    std::vector<SamplerBankPtr> retiredSamplers;   // awaiting reclamation (msg thread)
    sila::engine::VoiceMixer mixer;     // ../sila/engine/audio.py

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (SilaAudioProcessor)
};
