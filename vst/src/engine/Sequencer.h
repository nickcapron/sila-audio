#pragma once
#include <juce_core/juce_core.h>
#include "engine/Project.h"
#include <optional>

// Port of ../../sila/engine/sequencer.py (pure logic — no audio).
//
// Key plugin-world change vs. the Python source: position is DERIVED from the
// absolute 16th-note index instead of an internal mutable counter, so DAW
// loop/seek/relocate Just Work (the Python relative _advance would desync on a
// backward jump). counter = absSixteenth % stepCount; iteration = absSixteenth
// / stepCount. Standalone free-run still works (its index rises monotonically).
//
// Phase 4: the Sequencer is STATELESS w.r.t. the project — forEachTrig takes the
// (immutable) Project snapshot by const ref, plus the live performance scalars
// (songMode/fillActive) that the processor reads from APVTS params each block.
// This lets the audio thread evaluate whichever snapshot the RCU seam published
// without the sequencer holding a mutable pointer into shared state.
namespace sila::engine
{
// Emitted when a step fires (port of sequencer.py::TrigEvent).
struct TrigEvent
{
    const Track* track      = nullptr;
    int   trackIndex        = 0;     // index into Project::tracks (selects the sampler)
    int   stepIndex         = 0;
    int   velocity          = 100;
    int   pitchOffset       = 0;     // semitones; applied via per-voice varispeed
    float length            = 1.0f;
    int   microTiming       = 0;     // ±23 micro-steps
    int   retrig            = 1;     // re-trigger count within the step (1 = off)
    float retrigFade        = 0.0f;  // retrig velocity ramp (-1 fade out .. +1 swell)
    std::optional<float> pStart, pEnd;   // p_lock start/end overrides
    // Raw filter p-locks (passed through); the processor resolves them against the
    // APVTS slot params (the track base) at trigger — the engine stays structural.
    std::optional<float>      pCutoff, pResonance;
    std::optional<FilterMode> pFilterMode;
    // Resolved LFO config for this trigger (step p-lock overrides track base).
    LfoShape lfoShape       = LfoShape::Sine;
    LfoDest  lfoDest        = LfoDest::Cutoff;
    float    lfoRate        = 1.0f;
    float    lfoDepth       = 0.0f;
    bool     lfoSync        = true;
};

// Digitakt Song Mode (Phase 6). The active row/repeat/step is a PURE FUNCTION of
// the absolute transport position (resolveSong walks the row prefix-sums) — no
// audio-thread mutation, so a host loop/seek relocates exactly. The processor
// derives this each block/boundary and feeds it to forEachTrigSong.
struct SongPosition
{
    bool     valid       = false;   // false => no song authored => fall back to pattern mode
    bool     stopped     = false;   // past the last row with SongEnd::Stop (hold silent)
    int      row         = 0;       // active row index
    long     repeat      = 0;       // completed repeats of this row (drives A:B trig conditions)
    long     rowStep     = 0;       // step within the current repeat (0..rowLength-1)
    int      patternSlot = 0;       // PTN — which PatternBank slot this row plays
    uint8_t  mutes       = 0;       // row MUTE mask (authoritative in song mode)
    float    tempo       = 0.0f;    // row BPM override (<= 0 = global); applied in Standalone only
};

class Sequencer
{
public:
    // Evaluate every track of `project` at the absolute 16th index in PATTERN MODE
    // and invoke `fn` for each step that fires (mute/solo gating + trig condition +
    // probability — port of tick() + _evaluate_track). Every track plays the
    // project's currentPattern slot. Templated + zero-allocation so it is safe to
    // call on the audio thread; keep `fn`'s capture small. `project` must outlive
    // the call (the caller holds the snapshot's shared_ptr for the whole block).
    template <typename Fn>
    void forEachTrig (const Project& project, long absSixteenth, bool fillActive, Fn&& fn)
    {
        // Pattern mode: every track plays the project's currentPattern slot.
        const int activeSlot = juce::jlimit (0, PatternBank::kNumSlots - 1, project.currentPattern);

        bool anySolo = false;
        for (auto& t : project.tracks)
            if (t.solo) { anySolo = true; break; }

        int trackIndex = 0;
        for (auto& track : project.tracks)
        {
            const int ti = trackIndex++;
            if (track.muted)
                continue;
            if (anySolo && ! track.solo)
                continue;                        // silenced by another track's solo

            const std::vector<Step>& steps = resolveSteps (project, ti, activeSlot);
            const int stepCount = (int) steps.size();
            if (stepCount <= 0)
                continue;

            const long idx       = ((absSixteenth % stepCount) + stepCount) % stepCount;
            const long iteration = (absSixteenth - idx) / stepCount;   // exact for absSixteenth >= 0
            const Step& step = steps[(size_t) idx];

            if (! step.active)
                continue;
            if (! trigConditionPasses (step, iteration, fillActive))
                continue;
            if (! probabilityPasses (step.probability))
                continue;

            TrigEvent ev;
            fillTrigEvent (ev, track, ti, idx, step);
            fn (ev);
        }
    }

    // Song Mode (Phase 6) evaluation. The position (which slot/step/repeat/mutes)
    // is already resolved by resolveSong — this just gates + fires. Row mutes are
    // AUTHORITATIVE (they replace track mutes); solo still applies; the row repeat
    // index drives A:B trig conditions. Same zero-allocation contract as
    // forEachTrig: keep `fn`'s capture small; `project` must outlive the call.
    template <typename Fn>
    void forEachTrigSong (const Project& project, const SongPosition& song,
                          bool fillActive, Fn&& fn)
    {
        if (! song.valid || song.stopped)
            return;

        bool anySolo = false;
        for (auto& t : project.tracks)
            if (t.solo) { anySolo = true; break; }

        int trackIndex = 0;
        for (auto& track : project.tracks)
        {
            const int ti = trackIndex++;

            // Row MUTE is authoritative in song mode (overrides track.muted).
            if (song.mutes & (1u << (ti & 7)))
                continue;
            if (anySolo && ! track.solo)
                continue;

            // The row's pattern slot for this track; empty/unauthored => silent.
            const std::vector<Step>& steps = resolveSteps (project, ti, song.patternSlot);
            const int stepCount = (int) steps.size();
            if (stepCount <= 0)
                continue;

            // The pattern wraps inside the row (rowStep can exceed pattern length),
            // preserving per-track polyrhythm; the row length governs advancement.
            const long idx = ((song.rowStep % stepCount) + stepCount) % stepCount;
            const Step& step = steps[(size_t) idx];

            if (! step.active)
                continue;
            if (! trigConditionPasses (step, song.repeat, fillActive))   // iteration = row repeat
                continue;
            if (! probabilityPasses (step.probability))
                continue;

            TrigEvent ev;
            fillTrigEvent (ev, track, ti, idx, step);
            fn (ev);
        }
    }

    // Resolve the Digitakt song position at an absolute 16th index by walking the
    // active song's row prefix-sums (<=99 rows, integer-only, allocation-free).
    // songSixteenth is the absolute transport 16th (= host position since 0). The
    // result is a pure function of position, so a DAW loop/seek relocates exactly.
    static SongPosition resolveSong (const Project&, long songSixteenth);

private:
    static bool trigConditionPasses (const Step&, long iteration, bool fillActive);
    bool        probabilityPasses (int probability);

    // Build a TrigEvent from a fired step (p-locks + LFO resolution). Shared by
    // both pattern mode and song mode so the two paths can never drift.
    static void fillTrigEvent (TrigEvent&, const Track&, int trackIndex, long stepIndex, const Step&);

    // The Step vector for (trackIndex, slot) in the unified PatternBank — a const
    // reference, no copy. Empty (a static empty vector) when the slot is
    // unauthored or the track has no column => that track is silent for the slot.
    static const std::vector<Step>& resolveSteps (const Project&, int trackIndex, int slot);

    juce::Random rng;
};
} // namespace sila::engine
