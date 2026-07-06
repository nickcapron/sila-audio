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

    // Multi-out (Reaper per-track FX): a Main stereo bus (the full mix) plus one
    // stereo aux bus per lane. Each aux is stereo or disabled; Main is stereo.
    bool isBusesLayoutSupported (const BusesLayout& layouts) const override;

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

    // Phase 6: per-track params live in a fixed APVTS "slot" bank so they're host-
    // automatable. Tracks map to slots by index; a project can't exceed this.
    static constexpr int kMaxTracks = 8;

    // Latest transport position (quarter notes), published by processBlock for
    // the editor to read on the message thread (lock-free; C++ -> UI playhead).
    std::atomic<double> currentPpq { 0.0 };

    // Transport status, published by processBlock for the editor to read on the
    // message thread (port of /sequencer/status). The editor pushes these to the
    // UI as a "status" event on change, replacing the Python app's 2 s poll.
    std::atomic<bool>   transportPlaying { false };
    std::atomic<double> currentBpm       { kDefaultBpm };
    std::atomic<int>    currentSongSlot  { -1 };   // -1 = song mode off / not playing

    // Internal transport (UI play/stop + tempo). Governs playback when no host
    // transport is driving — i.e. always in Standalone, or a stopped DAW. A host
    // that is playing always takes priority (its transport + tempo win).
    std::atomic<bool>   internalPlaying { false };
    std::atomic<double> internalBpm     { kDefaultBpm };

    // Song Mode playhead (Phase 6), published once per block for the song-edit UI.
    // currentSongSlot above carries the active row's pattern slot in song mode.
    std::atomic<int>    currentSongRow    { -1 };  // -1 = not in a song
    std::atomic<int>    currentSongRepeat { 0 };

    // Bumped whenever a whole new Project is published (DAW state load). The
    // editor polls this on its timer to re-fetch GET /project — keeps the
    // processor decoupled from the editor (no cross-thread listener).
    std::atomic<uint32_t> projectEpoch { 0 };

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
    using SamplerBank    = std::vector<std::shared_ptr<sila::engine::Sampler>>;   // one slot's lanes
    using SamplerBankPtr = std::shared_ptr<const SamplerBank>;
    // Phase 7 (kit per pattern): a resident SET of banks, one per pattern slot
    // (null = unauthored/silent). The audio thread indexes by the active slot
    // (currentPattern, or the song's resolved slot); published/retired as one
    // immutable unit via RCU, exactly like the Project snapshot.
    using SamplerSet     = std::array<SamplerBankPtr, sila::engine::PatternBank::kNumSlots>;
    using SamplerSetPtr  = std::shared_ptr<const SamplerSet>;

    SamplerSetPtr samplerSnapshot() const { return liveSamplers.load (std::memory_order_acquire); }

    // Message thread: rebuild one (slot, lane)'s sampler from its layers and publish
    // a new set (other lanes/slots reuse their existing sampler + RR state). No-op
    // if the slot is out of range or the set is unset.
    void assignTrackSamples (int slot, int lane, const std::vector<sila::engine::SampleRef>& layers);

    // ~/SILA/library — root for resolving relative sample paths from the browser.
    static juce::File libraryRoot();

    // Audition a sample (library preview): decode + resample to the device rate on
    // the MESSAGE thread, then hand the prepared Sampler to the audio thread via a
    // lock-free atomic (consumed once in processBlock, spawned as a one-shot voice
    // through the master bus). Returns false if the file can't be decoded.
    bool auditionSample (const juce::File& file);

    // ~/SILA/projects — standalone project files (Save/Load).
    static juce::File projectsDir();

    // Load a project wholesale: build its sampler bank at the current rate, RCU-
    // swap it in, and bump projectEpoch so the editor refreshes. Message thread.
    void loadProject (ProjectPtr proj);

    // Track management (message thread). Both rebuild the project AND the parallel
    // sampler bank, then publish atomically via setProject (audio thread stays
    // lock-free; a removed track's sampler is kept alive by any ringing voice).
    void addTrack (const juce::String& name);   // appends; no-op at kMaxTracks
    void removeTrack (int index);                // erases the track + its sampler + pattern-bank column

    // Per-pattern mix recall (Phase 7c). captureLaneParams reads the live APVTS slot
    // params (vol/pan/cutoff/res/fmode) into proj.kits[slot] — call inside an
    // editProject mutator. recallLaneParams pushes kit[slot] back into APVTS (via
    // setValueNotifyingHost). Both message thread.
    void captureLaneParams (sila::engine::Project& proj, int slot);
    void recallLaneParams (int slot);

private:
    static juce::AudioProcessorValueTreeState::ParameterLayout makeParameters();

    // Build the bus layout: "Main" stereo + kMaxTracks "Track N" stereo aux buses
    // (one per lane), all enabled by default so a host like Reaper exposes them for
    // per-lane routing. See isBusesLayoutSupported.
    static BusesProperties makeBusesProperties();

    // Find the 16th-note boundaries inside this block, evaluate the Sequencer at
    // each, and spawn voices sample-accurately (swing + micro-timing folded into
    // the sample offset). Reads the immutable snapshot + live performance
    // scalars. Port of clock.py::_run timing, pull-based.
    void scheduleTriggers (const sila::engine::Project& project,
                           double ppqStart, double bpm, int numSamples,
                           float swing, bool songMode, bool fillActive);

    // The single voice-spawn path, shared by scheduleTriggers and live MIDI note
    // input so their DSP resolution can never drift. Audio thread, allocation-free.
    // `bank` = the active pattern slot's sampler bank (null => silent).
    void spawnVoice (const sila::engine::TrigEvent& ev, int startOffset,
                     const SamplerBank* bank, double samplesPer16);

    // Build the in-code demo project; returns the snapshot and (re)builds the
    // parallel sampler array. Replaced by UI-authored state in later steps.
    ProjectPtr buildDemoProject (double sampleRate);

    // Install the bundled factory sample pack (RD-6 + CZ-1 mini) into ~/SILA/library
    // on first run, so new users open to the showcase song with sound. Idempotent:
    // skips any file that already exists (never clobbers the user's library).
    void installFactoryLibrary();

    // Build a sampler for one track from its sample layers, resampling each file
    // to `sr` (message-thread file I/O). Shared by assignment + rate-change rebuild.
    static std::shared_ptr<sila::engine::Sampler>
        buildSamplerFromLayers (const std::vector<sila::engine::SampleRef>& layers, double sr);

    // Device-rate change (re-prepare): rebuild the sampler bank so file-backed
    // tracks are re-resampled to `sr`; the snapshot/edits are preserved. Called
    // from prepareToPlay only (no concurrent processBlock).
    void rebuildSamplerBankForRate (double sr);

    // Build the full sampler SET for a project: one bank per authored pattern slot
    // (from that slot's kit; empty kit => null/silent slot). Used when loading state.
    static SamplerSet buildBankForProject (const sila::engine::Project& proj, double sr);

    // Wholesale RCU swap of both Project and the sampler set (DAW state load). Audio
    // thread may be running, so old snapshots go on the retire lists; bumps
    // projectEpoch so the editor refreshes. Message thread only.
    void setProject (ProjectPtr proj, SamplerSetPtr bank);

    static juce::AudioBuffer<float> makeKick  (double sampleRate);
    static juce::AudioBuffer<float> makeSnare (double sampleRate);
    static juce::AudioBuffer<float> makeHat   (double sampleRate);

    static constexpr double kDefaultBpm = 120.0;   // standalone free-run tempo

    double sampleRate { 48000.0 };
    double internalPpq { 0.0 };       // free-running clock for the Standalone case
    long   lastFiredSixteenth { -1 }; // dedupe boundaries across blocks
    bool   wasInternalPlaying { false }; // edge-detect internal stop -> reset playhead
    bool   transportInitialized { false }; // set the wrapper-typed play default once

    // Live immutable project snapshot (RCU). Audio thread loads it per block.
    std::atomic<ProjectPtr> liveProject;
    // Superseded snapshots awaiting reclamation on the message thread.
    std::vector<ProjectPtr> retiredProjects;

    // Performance scalar not (yet) an APVTS param; read by the audio thread.
    std::atomic<bool> fillActive { false };

    // Library audition handoff: the message thread stores a prepared one-shot
    // Sampler here; processBlock exchanges it for null and spawns a voice. The
    // shared_ptr keeps the buffer alive (the voice pins it via keepAlive).
    std::atomic<std::shared_ptr<sila::engine::Sampler>> pendingAudition { nullptr };

    sila::engine::Sequencer  sequencer; // ../sila/engine/sequencer.py (stateless)

    // Live sampler bank (RCU). Audio thread loads it once per block; the message
    // thread swaps a track's sampler on assignment. Parallel to snapshot tracks.
    std::atomic<SamplerSetPtr> liveSamplers;
    std::vector<SamplerSetPtr> retiredSamplers;   // awaiting reclamation (msg thread)
    sila::engine::VoiceMixer mixer;     // ../sila/engine/audio.py

    // Per-track gain/pan, rebuilt each block from the snapshot and passed to the
    // mixer (continuous faders). Reused to avoid per-block allocation.
    std::vector<sila::engine::TrackMix> trackMix;

    // Per-track free-running LFO phase (radians), advanced each block. A free-run
    // (non-sync) voice samples its track's phase at trigger so overlapping voices
    // stay aligned to the track's LFO clock. Parallel to snapshot tracks.
    std::vector<double> trackLfoPhase;
    juce::Random        lfoRng;   // seeds per-voice sample-and-hold start value

    // Cached raw-value pointers for the per-slot APVTS bank (set in the ctor) so
    // the audio thread reads them with a single atomic load — no string lookups.
    std::atomic<float>* pVol[kMaxTracks]    {};
    std::atomic<float>* pPan[kMaxTracks]    {};
    std::atomic<float>* pCutoff[kMaxTracks] {};
    std::atomic<float>* pRes[kMaxTracks]    {};
    std::atomic<float>* pFmode[kMaxTracks]  {};

    // Cached pointers for the global params too — same rationale as the slot bank:
    // processBlock reads these every block, so resolve the string keys once here.
    std::atomic<float>* pSwing        = nullptr;
    std::atomic<float>* pSongMode     = nullptr;
    std::atomic<float>* pMasterVol    = nullptr;
    std::atomic<float>* pSmallSpeaker = nullptr;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (SilaAudioProcessor)
};
