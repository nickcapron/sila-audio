#pragma once
#include <juce_core/juce_core.h>
#include "engine/Project.h"
#include <vector>

// Single source of truth for Project <-> JSON (juce::var). Used by BOTH the
// editor bridge (GET /project, PUT step/sample edits) and the processor's DAW
// state (get/setStateInformation), so the wire format the UI speaks and the
// format persisted in the host session can never drift apart.
//
// `projectToVar`/`projectFromVar` cover the FULL structural Project (tracks +
// sample layers + pattern_bank + songs) — a superset of what the grid reads. Step
// data lives in the pattern_bank (unified, v3). Performance scalars (swing/
// songMode/bpm) are APVTS params, not part of this; the editor layers them onto
// the GET /project payload itself.
namespace sila::engine
{
constexpr int kProjectSchemaVersion = 6;   // v2 added `songs`; v3 moved step data
                                           // into the unified pattern_bank; v4 moved
                                           // the SOUND (samples + LFO) off the track
                                           // into a per-pattern kit (pattern_kits);
                                           // v5 added per-pattern lane visibility
                                           // (LaneSound.active — absent reads true);
                                           // v6 added the per-pattern mix snapshot
                                           // (vol/pan/cutoff/res/filter in LaneSound)

// Step / trig.
juce::var      stepToVar (const Step&);
void           applyStepVar (Step&, const juce::var&);   // applies onto an existing Step
const char*    trigToString (TrigCondition);
TrigCondition  trigFromString (const juce::String&);

// Sample layers (the { samples: [layer...] } shape).
std::vector<SampleRef> parseSampleLayers (const juce::var& samplesArray);

// Track (lane identity: id/name/color/muted/solo). The per-pattern sound lives in
// the kit (LaneSound) — the editor injects it into GET /project per lane.
juce::var trackToVar (const Track&);
Track     trackFromVar (const juce::var&);

// Per-pattern kit lane sound (Phase 7): { samples: [...], lfo: {...} }.
juce::var laneSoundToVar (const LaneSound&);

// Song Mode (Phase 6). Exposed so the editor bridge can serve/parse a single
// song for the song-edit routes without round-tripping the whole project.
juce::var  songRowToVar (const SongRow&);
SongRow    songRowFromVar (const juce::var&);
juce::var  songToVar (const Song&);
Song       songFromVar (const juce::var&);

// Full structural project: { schema_version, tracks, song_chain, pattern_bank }.
juce::var projectToVar (const Project&);
Project   projectFromVar (const juce::var&);
} // namespace sila::engine
