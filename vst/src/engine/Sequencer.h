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
    int   pitchOffset       = 0;     // carried, not applied until Phase 5
    float length            = 1.0f;
    int   microTiming       = 0;     // ±23 micro-steps
    std::optional<float> pStart, pEnd;   // p_lock start/end overrides
};

class Sequencer
{
public:
    // Evaluate every track of `project` at the absolute 16th index and invoke
    // `fn` for each step that fires (mute/solo gating + trig condition +
    // probability — port of tick() + _evaluate_track). `songMode`/`fillActive`
    // are the live performance scalars (from APVTS params). Templated +
    // zero-allocation so it is safe to call on the audio thread; keep `fn`'s
    // capture small. `project` must outlive the call (the caller holds the
    // snapshot's shared_ptr for the whole block).
    template <typename Fn>
    void forEachTrig (const Project& project, long absSixteenth,
                      bool songMode, bool fillActive, Fn&& fn)
    {
        // Song mode: the active pattern slot is a pure function of position
        // (no mutation, no allocation — see resolveSongSlot/resolveSteps).
        const int activeSlot = resolveSongSlot (project, absSixteenth, songMode);

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

            const std::vector<Step>& steps = resolveSteps (project, track, ti, activeSlot);
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
            ev.track       = &track;
            ev.trackIndex  = ti;
            ev.stepIndex   = (int) idx;
            ev.velocity    = step.velocity;
            ev.pitchOffset = step.pitchOffset;
            ev.length      = step.length;
            ev.microTiming = step.microTiming;
            ev.pStart      = step.pStart;
            ev.pEnd        = step.pEnd;
            fn (ev);
        }
    }

    // Active song-mode pattern slot for the given absolute position (-1 = off),
    // derived purely from position — no allocation/mutation, audio-thread safe.
    // Public so the processor can publish it as transport status. (Phase 3b.)
    static int  resolveSongSlot (const Project&, long absSixteenth, bool songMode);

private:
    static bool trigConditionPasses (const Step&, long iteration, bool fillActive);
    bool        probabilityPasses (int probability);

    // Song mode (Phase 3b): derived from the absolute position — no allocation
    // or mutation, safe to call on the audio thread.
    static const std::vector<Step>& resolveSteps (const Project&, const Track&,
                                                  int trackIndex, int activeSlot);
    static int  barLengthInSixteenths (const Project&);

    juce::Random rng;
};
} // namespace sila::engine
