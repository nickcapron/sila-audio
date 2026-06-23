# SILA VST3/AU/Standalone — JUCE plugin scaffold.
#
# Build (needs a C++20 toolchain; JUCE + VST3 SDK are fetched automatically):
#   cmake -B vst/build -S vst -DCMAKE_BUILD_TYPE=Release
#   cmake --build vst/build
#
# This is a SCAFFOLD: it wires up the plugin, the WebView editor, and the
# engine module layout. The engine bodies are stubs that reference the Python
# source in ../sila/engine as the spec (see DESIGN.md).

cmake_minimum_required(VERSION 3.22)
project(SILA VERSION 0.1.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include(FetchContent)
FetchContent_Declare(
    JUCE
    GIT_REPOSITORY https://github.com/juce-framework/JUCE.git
    GIT_TAG        8.0.4          # pin a known-good JUCE 8 release
)
FetchContent_MakeAvailable(JUCE)

juce_add_plugin(SILA
    PRODUCT_NAME            "SILA"
    COMPANY_NAME            "SILA"
    BUNDLE_ID               "audio.sila.plugin"
    PLUGIN_MANUFACTURER_CODE Sila
    PLUGIN_CODE             Sila
    ICON_BIG                "${CMAKE_CURRENT_SOURCE_DIR}/branding/sila-icon.png"
    ICON_SMALL              "${CMAKE_CURRENT_SOURCE_DIR}/branding/sila-icon.png"
    FORMATS                 VST3 AU Standalone
    VST3_AUTO_MANIFEST      FALSE         # skip the post-build moduleinfo step: it
                                          # LoadLibrary()s the freshly-built unsigned
                                          # plugin, which Windows Smart App Control
                                          # blocks. The .vst3 still loads fine in a
                                          # signed host (Reaper) — the manifest is
                                          # just an enumeration cache. (Public release
                                          # needs code-signing; see DESIGN/README.)
    COPY_PLUGIN_AFTER_BUILD TRUE          # auto-install the built plugin...
    VST3_COPY_DIR           "$ENV{USERPROFILE}/VST3"   # ...to a user-writable VST3 folder (no admin)
    IS_SYNTH                TRUE          # instrument: produces audio
    NEEDS_MIDI_INPUT        TRUE          # optional MIDI triggering
    EDITOR_WANTS_KEYBOARD_FOCUS TRUE
    NEEDS_WEB_BROWSER       TRUE          # Phase 4: WebView UI editor
    NEEDS_WEBVIEW2          TRUE          # Windows: statically link WebView2 (SDK via NuGet)
)

# Phase 4 (Step 1): bundle the bring-up UI + the vendored JUCE JS bridge module
# into the binary, served by the editor's resource provider. The full sila/ui
# gets added here as its REST surface is ported to the bridge.
juce_add_binary_data(SILAWebUI
    SOURCES
        ${CMAKE_CURRENT_SOURCE_DIR}/webui/index.html
        ${CMAKE_CURRENT_SOURCE_DIR}/webui/bridge.js
        ${CMAKE_CURRENT_SOURCE_DIR}/webui/js/juce/index.js
        ${CMAKE_CURRENT_SOURCE_DIR}/webui/js/juce/check_native_interop.js
)

# Factory sample pack (Behringer RD-6 kit + CZ-1 mini synth voices), embedded so
# brand-new users get it installed to ~/SILA/library on first run and open to the
# showcase song with sound (see SilaAudioProcessor::installFactoryLibrary). A
# distinct NAMESPACE/HEADER_NAME keeps it separate from the SILAWebUI BinaryData.
file(GLOB SILA_FACTORY_WAVS CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/factory/*.wav")
juce_add_binary_data(SILAFactory
    NAMESPACE   FactoryData
    HEADER_NAME SILAFactoryData.h
    SOURCES     ${SILA_FACTORY_WAVS})

target_sources(SILA PRIVATE
    src/PluginProcessor.cpp
    src/PluginEditor.cpp         # Phase 4 (WebView editor)
    src/engine/Sampler.cpp       # Phase 2
    src/engine/VoiceMixer.cpp    # Phase 2
    src/engine/Sequencer.cpp     # Phase 3
    src/engine/MidiExport.cpp     # song/pattern MIDI file export
    src/engine/ProjectJson.cpp    # Phase 4 Step 2e (shared Project <-> JSON)
    src/engine/Library.cpp        # Phase 8 (sample importer + library manager)
    # added as they are ported (see DESIGN.md roadmap):
    # src/engine/Fx.cpp          # Phase 5
)

target_include_directories(SILA PRIVATE src)

target_compile_definitions(SILA PUBLIC
    JUCE_WEB_BROWSER=1
    JUCE_USE_CURL=0
    JUCE_VST3_CAN_REPLACE_VST2=0
    JUCE_USE_WIN_WEBVIEW2_WITH_STATIC_LINKING=1   # enables the WebView2 backend + window.__JUCE__
)

target_link_libraries(SILA PRIVATE
    juce::juce_audio_utils
    juce::juce_dsp
    SILAWebUI                                     # bundled web UI (BinaryData)
    SILAFactory                                   # bundled factory sample pack (FactoryData)
    PUBLIC
    juce::juce_recommended_config_flags
    juce::juce_recommended_lto_flags
    juce::juce_recommended_warning_flags
)

# ── Unit tests ───────────────────────────────────────────────────────────────
# A tiny console app over the pure, security-critical engine pieces (the path-
# traversal guard + filename/pack sanitizers — the backstop for every library file
# op). No audio device / host needed. Build + run:
#   cmake --build vst/build --target SILA_Tests
#   "vst/build/SILA_Tests_artefacts/Debug/SILA Tests.exe"
juce_add_console_app(SILA_Tests PRODUCT_NAME "SILA Tests")
target_sources(SILA_Tests PRIVATE
    tests/test_main.cpp
    src/engine/Library.cpp)
target_include_directories(SILA_Tests PRIVATE src)
target_compile_definitions(SILA_Tests PRIVATE JUCE_USE_CURL=0 JUCE_WEB_BROWSER=0)
target_link_libraries(SILA_Tests PRIVATE
    juce::juce_core
    juce::juce_recommended_config_flags
    juce::juce_recommended_warning_flags)
