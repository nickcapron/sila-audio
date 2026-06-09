#pragma once

#include <juce_gui_extra/juce_gui_extra.h>
#include "PluginProcessor.h"

// Editor = the existing sila/ui running in a WebView. The browser calls native
// functions (the bridge) instead of FastAPI; the processor pushes events
// (playhead, song slot) back to the page. See vst/webui/bridge.js + DESIGN.md.
class SilaAudioProcessorEditor : public juce::AudioProcessorEditor
{
public:
    explicit SilaAudioProcessorEditor (SilaAudioProcessor&);
    ~SilaAudioProcessorEditor() override = default;

    void resized() override;

private:
    // Serve bundled UI (index.html / app.js / bridge.js) from BinaryData.
    std::optional<juce::WebBrowserComponent::Resource> serveResource (const juce::String& url);

    // One native function per REST endpoint the UI calls (addTrack, toggleStep,
    // setBpm, …). Maps the request to the engine and returns JSON.
    juce::var handleBackendCall (const juce::Array<juce::var>& args,
                                 juce::WebBrowserComponent::NativeFunctionCompletion);

    SilaAudioProcessor& processor;

    juce::WebBrowserComponent webView
    {
        juce::WebBrowserComponent::Options{}
            .withNativeIntegrationEnabled()
            .withResourceProvider ([this] (const auto& url) { return serveResource (url); })
            .withNativeFunction ("backendCall",
                [this] (auto& args, auto completion) { handleBackendCall (args, std::move (completion)); })
    };

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (SilaAudioProcessorEditor)
};
