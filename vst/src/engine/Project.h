#pragma once
#include <juce_core/juce_core.h>
#include <vector>
#include <array>
#include <optional>

// Port of ../../sila/models/step.py + project.py (the sequencer-relevant subset).
// Pure data — no audio, no serialization yet (preset state is Phase 5, the UI
// bridge that authors this data is Phase 4). FX/LFO/samples-as-data also land
// later; Phase 3 only needs what drives Sequencer timing + selection.
namespace sila::engine
{
// Port of step.py::TrigCondition.
enum class TrigCondition { Always, OneIn2, OneIn4, Fill, NotFill };

// Port of step.py::Step.
struct Step
{
    bool          active      = false;
    int           velocity    = 100;                  // 0..127
    int           pitchOffset = 0;                    // semitones; carried, applied in Phase 5 (needs resampling)
    int           probability = 100;                  // 0..100
    TrigCondition trig        = TrigCondition::Always;
    float         length      = 1.0f;                 // note-length multiplier
    int           microTiming = 0;                    // ±23 micro-steps (1/96-note); + = late
    std::optional<float> pStart, pEnd;                // p_locks["start"/"end"], 0..1 fractions
};

// Port of project.py SampleLayer (load-relevant subset). A track's sound is one
// or more velocity-layered sample files. The processor's sampler bank is
// (re)built from these on the message thread when they change; the audio thread
// never touches files. `path` is absolute, or relative to ~/SILA/library.
struct SampleRef
{
    juce::String path;
    int   velMin = 0, velMax = 127;
    float start = 0.0f, end = 1.0f;     // 0..1 fractions (layer-level; step p-locks override)
    int   rrGroup = 0;
};

// Port of project.py::TrackModel. `steps.size()` is the per-track loop length,
// so tracks of different lengths run as polyrhythms (matches the Python
// per-track counter). FX/LFO/humanize come with later phases.
struct Track
{
    juce::String           id;
    juce::String           name;
    bool                   muted = false;
    bool                   solo  = false;
    std::vector<Step>      steps;
    std::vector<SampleRef> samples;     // velocity layers; empty = synthesized/unset
};

// Port of project.py::PatternBank. kNumSlots named pattern snapshots; each slot
// stores one Step vector per track, parallel to Project::tracks. An empty
// per-track entry means "no pattern stored for this track in this slot" → the
// Sequencer falls back to the track's live `steps`. Authored off the audio
// thread (setup now, the UI bridge in Phase 4); the audio thread only reads it.
struct PatternBank
{
    static constexpr int kNumSlots = 8;
    std::array<std::vector<std::vector<Step>>, kNumSlots> slots;
};

// Port of project.py::ProjectModel (structural subset).
//
// Phase 4: this is the immutable RCU snapshot the audio thread reads. It holds
// only STRUCTURE (tracks/steps/chain/bank). The performance scalars
// (swing/songMode/fillActive) are live APVTS params/atomics on the processor —
// kept out of here so the published snapshot is truly read-only on the audio
// thread.
struct Project
{
    std::vector<Track> tracks;

    // Song mode (Phase 3b): a chain of slot indices played one bar each. The
    // active slot is DERIVED from the transport position on the audio thread
    // (Sequencer::resolveSongSlot) — never swapped/mutated there.
    std::vector<int> songChain;     // ordered slot indices into patternBank
    PatternBank      patternBank;
};
} // namespace sila::engine
