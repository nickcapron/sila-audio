#pragma once

#include <juce_audio_processors/juce_audio_processors.h>
#include <juce_gui_extra/juce_gui_extra.h>
#include "PluginProcessor.h"
#include <optional>
#include <limits>

// Phase 4 (Step 2a): the editor hosts the vanilla HTML/JS UI in a JUCE 8
// WebBrowserComponent (WebView2 on Windows) and bridges it to the engine:
//   C++ -> UI : a "playhead" event pushed each timer tick (PPQ position).
//   UI  -> C++: a WebToggleButtonRelay bound to the "songMode" APVTS parameter,
//               and a "backendCall" native function (REST-shaped: method/path/body)
//               that reads/edits the engine over the RCU snapshot seam.
// All edits run on the message thread and publish a new immutable snapshot via
// SilaAudioProcessor::editProject(); the audio thread only ever reads.
class SilaAudioProcessorEditor : public juce::AudioProcessorEditor,
                                 private juce::Timer
{
public:
    explicit SilaAudioProcessorEditor (SilaAudioProcessor&);
    ~SilaAudioProcessorEditor() override;

    void paint (juce::Graphics&) override;
    void resized() override;

private:
    void timerCallback() override;

    // Serve the bundled UI from BinaryData via the resource provider.
    std::optional<juce::WebBrowserComponent::Resource> serveResource (const juce::String& url);

    // REST-shaped bridge: args = [method, path, body]. Runs on the message
    // thread; routes to GET project / step + track edits over editProject().
    juce::var handleBackendCall (const juce::Array<juce::var>& args);

    // Song Mode (Phase 6): the active song + song list + limits, the payload the
    // song-edit grid renders from and every /song mutation returns.
    juce::var songStateVar() const;

    float currentSwing() const;
    bool  currentSongMode() const;

    // Per-track APVTS slot bank (Phase 6) helpers.
    int   trackSlot (const juce::String& id) const;             // track index by id, or -1
    float slotValue (int slot, const juce::String& pid) const;  // raw param value
    void  setSlotValue (int slot, const juce::String& pid, float value);  // via setValueNotifyingHost

    // Launch a native save dialog, then bounce the active song (or current pattern)
    // to a Standard MIDI File there; pushes the result via the "midi-export" event.
    void launchMidiExport();

    // Launch the native folder picker for the sample importer; pushes the chosen
    // folder back to the UI via the "import-folder" event (then the UI scans it).
    void launchImportBrowse();

    SilaAudioProcessor& processor;

    // Kept alive while the async FileChooser dialog is open.
    std::unique_ptr<juce::FileChooser> fileChooser;

    // Declared before webView: the browser Options reference the relay.
    juce::WebToggleButtonRelay songModeRelay { "songModeToggle" };

    juce::WebBrowserComponent webView;

    // Binds the relay to the APVTS "songMode" parameter (two-way, lock-free).
    juce::WebToggleButtonParameterAttachment songModeAttachment;

    double lastSentPpq = std::numeric_limits<double>::quiet_NaN();

    // Last transport status pushed to the UI, so the 30 Hz timer only emits a
    // "status" event when something actually changed (port of the 2 s poll).
    bool   lastSentPlaying  = false;
    double lastSentBpm      = 0.0;
    int    lastSentSongSlot = -2;   // -2 = nothing sent yet (-1 is a real value)
    int    lastSentSongRow  = -2;   // song-mode playhead row (-2 = nothing sent yet)

    // Last project epoch the UI has seen; a change means the processor swapped in
    // a whole new Project (DAW state load) and the grid must re-fetch.
    uint32_t lastSeenEpoch = 0;

    // Last per-slot vol/pan pushed to the UI, so the timer only emits a "params"
    // event when a value changed (host automation / generic editor -> UI).
    float lastVol[SilaAudioProcessor::kMaxTracks]    {};
    float lastPan[SilaAudioProcessor::kMaxTracks]    {};
    float lastCutoff[SilaAudioProcessor::kMaxTracks] {};
    float lastRes[SilaAudioProcessor::kMaxTracks]    {};
    float lastFmode[SilaAudioProcessor::kMaxTracks]  { -1, -1, -1, -1, -1, -1, -1, -1 };

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (SilaAudioProcessorEditor)
};
