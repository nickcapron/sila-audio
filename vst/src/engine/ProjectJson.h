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
// steps + sample layers + song chain + pattern bank) — a superset of what the
// grid reads. Performance scalars (swing/songMode/bpm) are APVTS params, not
// part of this; the editor layers them onto the GET /project payload itself.
namespace sila::engine
{
constexpr int kProjectSchemaVersion = 1;

// Step / trig.
juce::var      stepToVar (const Step&);
void           applyStepVar (Step&, const juce::var&);   // applies onto an existing Step
const char*    trigToString (TrigCondition);
TrigCondition  trigFromString (const juce::String&);

// Sample layers (the { samples: [layer...] } shape).
std::vector<SampleRef> parseSampleLayers (const juce::var& samplesArray);

// Track (includes UI-friendly id/name/muted/solo/step_count + steps + samples).
juce::var trackToVar (const Track&);
Track     trackFromVar (const juce::var&);

// Full structural project: { schema_version, tracks, song_chain, pattern_bank }.
juce::var projectToVar (const Project&);
Project   projectFromVar (const juce::var&);
} // namespace sila::engine
