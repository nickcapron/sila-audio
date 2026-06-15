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

// Port of project.py::TrackModel. The track no longer owns its step data —
// steps live in the PatternBank (unified Phase 6), so the same track plays a
// different pattern per slot. The track keeps only its identity, gating, LFO
// config and sample layers.
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
    std::vector<SampleRef> samples;     // velocity layers; empty = synthesized/unset
};

// Default steps in a freshly-materialized pattern (one 4/4 bar of 16ths).
constexpr int kDefaultPatternLength = 16;

// Unified pattern store (Phase 6). kNumSlots named patterns (A01..A16); each slot
// holds one Step vector PER TRACK, parallel to Project::tracks by index. A slot is
// either EMPTY (unauthored => silent, blank grid) or "materialized" to exactly
// tracks.size() columns of equal length (master length per pattern). The grid
// edits the project's currentPattern slot; Song Mode rows pick a slot by index.
// Read-only on the audio thread; authored on the message thread via editProject.
struct PatternBank
{
    static constexpr int kNumSlots = 16;
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
// only STRUCTURE (tracks / pattern bank / songs). The performance scalars
// (swing/songMode/fillActive) are live APVTS params/atomics on the processor —
// kept out of here so the published snapshot is truly read-only on the audio
// thread.
struct Project
{
    static constexpr int kMaxSongs = 16;    // Elektron caps a project at 16 songs

    std::vector<Track> tracks;

    // Unified pattern bank (Phase 6): the single source of truth for step data.
    // currentPattern is the slot the grid edits and pattern mode plays. Both read
    // lock-free on the audio thread; switched on the message thread via editProject.
    PatternBank patternBank;
    int         currentPattern = 0;     // 0..kNumSlots-1

    // Digitakt Song Mode (Phase 6). The active song plays when songMode is on.
    std::vector<Song> songs;        // up to kMaxSongs
    int               activeSong = 0;   // which song plays (structural, read lock-free)
};

// Materialize a pattern slot to exactly tracks.size() columns of equal length,
// filling any missing/empty column with inactive steps (master length per
// pattern). No-op for a valid already-rectangular slot. Message thread only
// (mutates the Project copy inside editProject). The length is taken from the
// slot's first non-empty column, else kDefaultPatternLength.
inline void ensurePatternColumns (Project& p, int slot)
{
    if (slot < 0 || slot >= PatternBank::kNumSlots)
        return;
    auto& cols = p.patternBank.slots[(size_t) slot];

    int len = kDefaultPatternLength;
    for (const auto& c : cols)
        if (! c.empty()) { len = (int) c.size(); break; }

    cols.resize (p.tracks.size());
    for (auto& c : cols)
        if ((int) c.size() != len)
            c.assign ((size_t) len, Step{});
}
} // namespace sila::engine
