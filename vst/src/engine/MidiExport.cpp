#include "engine/MidiExport.h"
#include "engine/Sequencer.h"
#include <cmath>
#include <vector>

namespace sila::engine
{
juce::String MidiExportResult::summary() const
{
    if (empty)
        return "nothing to export (pattern/song is empty)";

    const juce::String mode = wroteSong ? "song" : "pattern";
    return juce::String (notes) + " notes, " + juce::String (tracks) + " tracks ("
         + mode + ", " + juce::String (lengthSixteenths) + " steps)";
}

namespace
{
constexpr int    kPPQ      = 960;            // ticks per quarter note
constexpr double kTicks16  = kPPQ / 4.0;     // one 16th note in ticks (240)
constexpr int    kBaseNote = 60;             // C3 — drum lanes sit here; pitch offset transposes

int microsPerQuarter (double bpm)
{
    const double b = bpm > 1.0 ? bpm : 120.0;
    return (int) std::llround (60'000'000.0 / b);
}

// The current pattern's master length (first non-empty column), 0 if unauthored.
int patternLength (const Project& p, int slot)
{
    if (slot < 0 || slot >= PatternBank::kNumSlots)
        return 0;
    for (const auto& col : p.patternBank.slots[(size_t) slot])
        if (! col.empty())
            return (int) col.size();
    return 0;
}
} // namespace

juce::MidiFile buildProjectMidi (const Project& project, double bpm, float swing,
                                 MidiExportResult& result)
{
    juce::MidiFile mf;
    mf.setTicksPerQuarterNote (kPPQ);

    const int numTracks = (int) project.tracks.size();
    if (numTracks <= 0)
    {
        result.empty = true;
        return mf;
    }

    // One note sequence per lane, plus the tempo/meta track.
    std::vector<juce::MidiMessageSequence> laneSeq ((size_t) numTracks);
    juce::MidiMessageSequence meta;
    meta.addEvent (juce::MidiMessage::timeSignatureMetaEvent (4, 4));

    double maxTick = 0.0;

    // Emit a note-on/note-off pair into a lane sequence (ticks, clamped values).
    auto emitNote = [&] (int lane, int channel, double onTick, double lenTicks, int note, int vel)
    {
        note = juce::jlimit (0, 127, note);
        vel  = juce::jlimit (1, 127, vel);
        if (onTick < 0.0) onTick = 0.0;
        const double offTick = onTick + juce::jmax (1.0, lenTicks);

        auto& s = laneSeq[(size_t) lane];
        s.addEvent (juce::MidiMessage::noteOn  (channel, note, (juce::uint8) vel), onTick);
        s.addEvent (juce::MidiMessage::noteOff (channel, note),                    offTick);
        ++result.notes;
        maxTick = juce::jmax (maxTick, offTick);
    };

    // Place one fired trig on the grid at absolute 16th `abs16`, mirroring the
    // engine's swing + micro-timing + retrig so the bounce matches what plays.
    auto handleTrig = [&] (const TrigEvent& ev, long abs16)
    {
        if (ev.trackIndex < 0 || ev.trackIndex >= numTracks)
            return;
        const int channel = (ev.trackIndex % 16) + 1;   // lane 0 -> ch 1

        double tick = (double) abs16 * kTicks16;
        // Swing: odd 16ths shift (port of scheduleTriggers' -swingOffset on odd idx).
        if (abs16 & 1)
            tick -= (double) swing * kTicks16 * 0.5;
        // Micro-timing: the engine applies the late side only (block-edge clamp);
        // mirror that here so the file lines up with the audio.
        const double mt = ev.microTiming * kTicks16 / 6.0;
        if (mt > 0.0)
            tick += mt;

        const int    note = kBaseNote + ev.pitchOffset;
        const double gate = (ev.length > 0.0f) ? (double) ev.length * kTicks16
                                               : kTicks16;     // one-shot -> 1/16 note

        const int rt = juce::jlimit (1, 8, ev.retrig);
        if (rt <= 1)
        {
            emitNote (ev.trackIndex, channel, tick, gate, note, ev.velocity);
            return;
        }

        // Retrig (ratchet): rt evenly-spaced hits across the step with the same
        // velocity ramp the engine applies (+ swell / - fade).
        const double spacing = kTicks16 / (double) rt;
        const float  fade    = juce::jlimit (-1.0f, 1.0f, ev.retrigFade);
        for (int k = 0; k < rt; ++k)
        {
            const float t    = (float) k / (float) (rt - 1);
            const float mult = fade >= 0.0f ? (1.0f - fade * (1.0f - t))
                                            : (1.0f + fade * t);
            const int   v    = (int) std::lround (ev.velocity * mult);
            emitNote (ev.trackIndex, channel, tick + k * spacing,
                      juce::jmin (gate, spacing), note, v);
        }
    };

    Sequencer seq;

    // Bounce the active song if one is authored, else the current pattern.
    const Song* song = nullptr;
    if (project.activeSong >= 0 && project.activeSong < (int) project.songs.size()
        && ! project.songs[(size_t) project.activeSong].rows.empty())
        song = &project.songs[(size_t) project.activeSong];

    if (song != nullptr)
    {
        result.wroteSong = true;

        // Total length + per-row tempo meta events (placed at each row's start; the
        // tick grid is tempo-independent, so this only affects playback speed).
        long total16 = 0;
        for (const auto& row : song->rows)
        {
            const double tempo = (row.tempo > 0.0f) ? (double) row.tempo : bpm;
            meta.addEvent (juce::MidiMessage::tempoMetaEvent (microsPerQuarter (tempo)),
                           (double) total16 * kTicks16);
            total16 += (long) juce::jmax (1, row.repeat) * (long) juce::jmax (1, row.length);
        }
        result.lengthSixteenths = total16;

        for (long abs16 = 0; abs16 < total16; ++abs16)
        {
            const SongPosition pos = Sequencer::resolveSong (project, abs16);
            if (! pos.valid || pos.stopped)
                break;
            seq.forEachTrigSong (project, pos, /*fillActive*/ false,
                                 [&] (const TrigEvent& ev) { handleTrig (ev, abs16); });
        }
    }
    else
    {
        meta.addEvent (juce::MidiMessage::tempoMetaEvent (microsPerQuarter (bpm)), 0.0);

        const int slot = juce::jlimit (0, PatternBank::kNumSlots - 1, project.currentPattern);
        const int len  = patternLength (project, slot);
        result.lengthSixteenths = len;

        for (long abs16 = 0; abs16 < len; ++abs16)
            seq.forEachTrig (project, abs16, /*fillActive*/ false,
                             [&] (const TrigEvent& ev) { handleTrig (ev, abs16); });
    }

    if (result.notes == 0)
    {
        result.empty = true;
        return mf;
    }

    // End-of-file tick: past the last note and at least the bounced length.
    const double endTick = juce::jmax (maxTick, (double) result.lengthSixteenths * kTicks16);

    meta.addEvent (juce::MidiMessage::endOfTrack(), endTick);
    meta.updateMatchedPairs();
    mf.addTrack (meta);

    for (int i = 0; i < numTracks; ++i)
    {
        auto& s = laneSeq[(size_t) i];
        s.addEvent (juce::MidiMessage::textMetaEvent (3, project.tracks[(size_t) i].name), 0.0);
        s.addEvent (juce::MidiMessage::endOfTrack(), endTick);
        s.sort();
        s.updateMatchedPairs();
        mf.addTrack (s);
    }
    result.tracks = numTracks;

    return mf;
}
} // namespace sila::engine
