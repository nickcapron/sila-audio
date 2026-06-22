#pragma once
#include <juce_audio_basics/juce_audio_basics.h>
#include "engine/Project.h"

// Song-mode / pattern MIDI export.
//
// Bounces the project's sequence to a Standard MIDI File (Type 1) by REUSING the
// live Sequencer to evaluate every step — so the exported notes match exactly
// what plays (mute/solo, trig conditions, probability, swing, micro-timing and
// retrig all resolved through the same code path the audio thread uses). One MIDI
// track per project lane, each on its own MIDI channel (lane 0 -> ch 1, ...), so
// the file lines up with the per-lane multi-out routing planned for Reaper.
//
// This is a one-time bake, not a live performance: probability is rolled once and
// fill is treated as inactive. Pitch is exported as a base note (C3) transposed by
// the step's pitch offset; a drum lane therefore sits at C3 while a melodic lane
// transposes. The build runs entirely off the immutable Project snapshot on the
// message thread — no audio-thread or file interaction here (the caller writes the
// returned MidiFile).
namespace sila::engine
{
struct MidiExportResult
{
    int  notes            = 0;       // note-on events written
    int  tracks           = 0;       // lane tracks written (excludes the tempo track)
    long lengthSixteenths = 0;       // total 16th steps bounced
    bool wroteSong        = false;   // true = active song bounced; false = current pattern
    bool empty            = false;   // true = nothing authored to export

    juce::String summary() const;
};

// Build the SMF. If the active song has rows it is bounced in full (one pass; a
// Loop-ending song is written once); otherwise the current pattern is bounced once
// at its master length. `bpm` is the global/host tempo (per-row tempo overrides are
// written as tempo meta events in song mode); `swing` is the live swing amount
// (0..1) so the bounce matches the engine's groove.
juce::MidiFile buildProjectMidi (const Project& project, double bpm, float swing,
                                 MidiExportResult& result);
} // namespace sila::engine
