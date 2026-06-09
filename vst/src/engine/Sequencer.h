#pragma once
#include <juce_core/juce_core.h>
#include <vector>

// Port of ../../sila/engine/sequencer.py  (pure logic — no audio).
// Each track has its own step counter (polyrhythm); tick() advances all
// unmuted/solo-respecting tracks once and returns the events that fired.
namespace sila::engine
{
struct TrigEvent
{
    juce::String trackId;
    int   stepIndex   = 0;
    int   velocity    = 100;
    int   pitchOffset = 0;
    float length      = 1.0f;   // step note-length multiplier
    int   microTiming = 0;      // ±23 micro-steps (applied as a sample offset)
    // p_locks (start/end overrides) → carried as optional fields when ported.
};

class Sequencer
{
public:
    // TODO port from sequencer.py:
    //   - per-track counters + iteration counts
    //   - trig conditions (ALWAYS/FILL/NOT_FILL/1:2/1:4) and probability
    //   - mute/solo gating
    std::vector<TrigEvent> tick();   // advance one 16th note
    void reset();
    void addTrack (const juce::String& id);
    void removeTrack (const juce::String& id);
};
} // namespace sila::engine
