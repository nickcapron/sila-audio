#include "engine/Sequencer.h"

namespace sila::engine
{
// Port of sequencer.py::_trig_condition_passes. `iteration` is the number of
// completed pattern loops (derived from the absolute index), so the
// deterministic A:B conditions stay stable under host loop/seek. `fillActive`
// is the live performance scalar (was project.fill_active).
bool Sequencer::trigConditionPasses (const Step& step, long iteration, bool fillActive)
{
    switch (step.trig)
    {
        case TrigCondition::Always:  return true;
        case TrigCondition::Fill:    return fillActive;
        case TrigCondition::NotFill: return ! fillActive;
        case TrigCondition::OneIn2:  return (iteration % 2) == 0;
        case TrigCondition::OneIn4:  return (iteration % 4) == 0;
    }
    return false;
}

// Port of sequencer.py::_probability_passes (random.randint(1, 100) <= p).
bool Sequencer::probabilityPasses (int probability)
{
    if (probability >= 100) return true;
    if (probability <= 0)   return false;
    return rng.nextInt (juce::Range<int> (1, 101)) <= probability;   // 1..100 inclusive
}

// One "bar" = the longest track's pattern length, in 16ths (port of clock.py's
// bar_len = max(len(t.steps))). Derived from the live patterns so it stays a
// fixed grid regardless of which song slot is active. >= 1.
int Sequencer::barLengthInSixteenths (const Project& project)
{
    int maxLen = 0;
    for (const auto& t : project.tracks)
        maxLen = juce::jmax (maxLen, (int) t.steps.size());
    return juce::jmax (1, maxLen);
}

// The active song slot for this position, or -1 when song mode is off / no
// chain. Pure function of absSixteenth — seek/loop-safe, allocation-free.
int Sequencer::resolveSongSlot (const Project& project, long absSixteenth, bool songMode)
{
    if (! songMode || project.songChain.empty())
        return -1;

    const int  barLen   = barLengthInSixteenths (project);
    const long absBar    = absSixteenth / barLen;               // floor for absSixteenth >= 0
    const int  len       = (int) project.songChain.size();
    const int  chainPos  = (int) (((absBar % len) + len) % len);
    return project.songChain[(size_t) chainPos];
}

// The Step vector to evaluate for this track: the active slot's stored pattern,
// or the track's live `steps` when song mode is off or the slot has no entry
// for this track. Returns a const reference — no copy.
const std::vector<Step>& Sequencer::resolveSteps (const Project& project, const Track& track,
                                                  int trackIndex, int activeSlot)
{
    if (activeSlot >= 0 && activeSlot < PatternBank::kNumSlots)
    {
        const auto& slot = project.patternBank.slots[(size_t) activeSlot];
        if (trackIndex < (int) slot.size() && ! slot[(size_t) trackIndex].empty())
            return slot[(size_t) trackIndex];
    }
    return track.steps;
}

// Shared TrigEvent builder (pattern mode + song mode). Copies the raw step fields
// + p-lock optionals through; the processor resolves them against the APVTS base.
void Sequencer::fillTrigEvent (TrigEvent& ev, const Track& track, int trackIndex,
                               long stepIndex, const Step& step)
{
    ev.track       = &track;
    ev.trackIndex  = trackIndex;
    ev.stepIndex   = (int) stepIndex;
    ev.velocity    = step.velocity;
    ev.pitchOffset = step.pitchOffset;
    ev.length      = step.length;
    ev.microTiming = step.microTiming;
    ev.pStart      = step.pStart;
    ev.pEnd        = step.pEnd;
    // Filter p-locks pass through; the processor resolves vs the APVTS base.
    ev.pCutoff     = step.pCutoff;
    ev.pResonance  = step.pResonance;
    ev.pFilterMode = step.pFilterMode;
    // Resolve LFO: depth/rate are p-lockable; shape/dest/sync track-level.
    ev.lfoShape    = track.lfoShape;
    ev.lfoDest     = track.lfoDest;
    ev.lfoRate     = step.pLfoRate.value_or (track.lfoRate);
    ev.lfoDepth    = step.pLfoDepth.value_or (track.lfoDepth);
    ev.lfoSync     = track.lfoSync;
}

// Walk the active song's rows, summing each row's span (length * repeat) in 16ths,
// to find which row/repeat/step the absolute position lands in. Pure function of
// position (no mutation), <=99 iterations, integer-only — safe on the audio thread
// and exactly seek/loop-relocatable.
SongPosition Sequencer::resolveSong (const Project& project, long songSixteenth)
{
    SongPosition out;
    if (project.songs.empty())
        return out;                                   // valid stays false => pattern fallback

    const int si = juce::jlimit (0, (int) project.songs.size() - 1, project.activeSong);
    const Song& song = project.songs[(size_t) si];
    if (song.rows.empty())
        return out;

    // Total span of the whole song, in 16ths.
    long total = 0;
    for (const auto& r : song.rows)
        total += (long) r.length * (long) r.repeat;
    if (total <= 0)
        return out;

    long pos = songSixteenth < 0 ? 0 : songSixteenth;
    if (song.end == SongEnd::Loop)
    {
        pos %= total;                                 // wrap to the top
    }
    else if (pos >= total)                            // Stop: past the end => hold silent
    {
        out.valid   = true;
        out.stopped = true;
        out.row     = (int) song.rows.size() - 1;
        return out;
    }

    for (int r = 0; r < (int) song.rows.size(); ++r)
    {
        const SongRow& row = song.rows[(size_t) r];
        const long span = (long) row.length * (long) row.repeat;
        if (pos < span)
        {
            out.valid       = true;
            out.row         = r;
            out.repeat      = pos / row.length;
            out.rowStep     = pos % row.length;
            out.patternSlot = row.patternSlot;
            out.mutes       = row.mutes;
            out.tempo       = row.tempo;
            return out;
        }
        pos -= span;
    }
    return out;   // unreachable (pos < total guarantees a hit) — defensive
}
} // namespace sila::engine
