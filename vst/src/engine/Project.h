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

// LFO (Phase 5). Per-VOICE in the VST (each note its own phase) — a deliberate
// divergence from lfo.py's per-track processor, so p-locked modulation is per
// note and tails keep modulating. Shapes match lfo.py (sine/triangle/square/
// sawtooth); `random` is a true sample-and-hold (new value each cycle).
enum class LfoShape { Sine, Triangle, Square, Sawtooth, Random };
enum class LfoDest  { Cutoff, Volume, Pitch };   // pan deferred (per-track mix stage)

// Filter mode — the TPT SVF exposes all three from the same state.
enum class FilterMode { LowPass, HighPass, BandPass };

// Port of step.py::Step.
struct Step
{
    bool          active      = false;
    int           velocity    = 100;                  // 0..127
    int           pitchOffset = 0;                    // semitones; carried, applied in Phase 5 (needs resampling)
    int           probability = 100;                  // 0..100
    TrigCondition trig        = TrigCondition::Always;
    float         length      = 0.0f;                 // gate in 16ths; <= 0 = one-shot (no gate)
    int           microTiming = 0;                    // ±23 micro-steps (1/96-note); + = late
    std::optional<float> pStart, pEnd;                // p_locks["start"/"end"], 0..1 fractions
    std::optional<float> pCutoff, pResonance;         // p_locks["cutoff"/"resonance"], override track base
    std::optional<float> pLfoDepth, pLfoRate;         // p_locks["lfo_depth"/"lfo_rate"]
    std::optional<FilterMode> pFilterMode;            // p_locks["filter_mode"], override track base
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
    // volume/pan/cutoff/resonance/filterMode moved to the APVTS slot bank
    // (Phase 6, host-automatable). Per-step p-locks (below, on Step) still override.
    // LFO (per-voice). depth 0 = off (zero cost). sync = retrigger phase per note.
    LfoShape               lfoShape = LfoShape::Sine;
    LfoDest                lfoDest  = LfoDest::Cutoff;
    float                  lfoRate  = 1.0f;   // Hz (Speed)
    float                  lfoDepth = 0.0f;   // 0..1
    bool                   lfoSync  = true;   // true = trig-sync, false = free-run
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

// Digitakt-style Song Mode (Phase 6). A Song is an arrangement of pattern rows
// played in sequence; a project holds up to kMaxSongs of them. Each SongRow is a
// 1:1 mapping of the Elektron song editor columns. The audio thread reads these
// as immutable data and DERIVES the active row/repeat/step from the absolute
// transport position (see Sequencer::resolveSong) — no mutation on the hot path,
// so a host loop/seek relocates exactly.
struct SongRow
{
    juce::String label;            // LABEL — free text ("VERSE", "CHORUS", a pattern name)
    int     patternSlot = 0;       // PTN  — index into PatternBank::slots (0..kNumSlots-1)
    int     repeat      = 1;       // ↺    — 1..32  times the row repeats before advancing
    int     length      = 16;      // +I   — 2..1024 steps per repeat (overrides pattern length)
    float   tempo       = 0.0f;    // BPM  — per-row override; <= 0 = use global/host tempo
    uint8_t mutes       = 0;       // MUTE — bit s set => track slot s muted for this row (8 slots)
};

// What happens after the last row finishes — the Elektron "end behaviour" row.
enum class SongEnd { Loop, Stop };

struct Song
{
    juce::String         name;
    std::vector<SongRow> rows;              // up to kMaxRows
    SongEnd              end = SongEnd::Loop;

    static constexpr int kMaxRows = 99;     // Elektron caps a song at 99 rows
};

// Port of project.py::ProjectModel (structural subset).
//
// Phase 4: this is the immutable RCU snapshot the audio thread reads. It holds
// only STRUCTURE (tracks/steps/chain/bank/songs). The performance scalars
// (swing/songMode/fillActive) are live APVTS params/atomics on the processor —
// kept out of here so the published snapshot is truly read-only on the audio
// thread.
struct Project
{
    static constexpr int kMaxSongs = 16;    // Elektron caps a project at 16 songs

    std::vector<Track> tracks;

    // Legacy Phase 3b "song chain" (one slot per bar). Kept for back-compat
    // deserialization + as a fallback when no Song is authored; superseded by the
    // `songs` arrangement below for real Song Mode.
    std::vector<int> songChain;     // ordered slot indices into patternBank
    PatternBank      patternBank;

    // Digitakt Song Mode (Phase 6). The active song plays when songMode is on.
    std::vector<Song> songs;        // up to kMaxSongs
    int               activeSong = 0;   // which song plays (structural, read lock-free)
};
} // namespace sila::engine
