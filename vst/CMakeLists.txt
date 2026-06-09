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
    FORMATS                 VST3 AU Standalone
    IS_SYNTH                TRUE          # instrument: produces audio
    NEEDS_MIDI_INPUT        TRUE          # optional MIDI triggering
    EDITOR_WANTS_KEYBOARD_FOCUS TRUE
    # NEEDS_WEB_BROWSER     TRUE          # re-enable in Phase 4 (WebView editor)
)

# Phase 4: bundle the web UI (sila/ui) into the binary. Off for the Phase 2
# audio build so the WebView API surface can't break the build.
# juce_add_binary_data(SILAWebUI
#     SOURCES
#         ${CMAKE_CURRENT_SOURCE_DIR}/../sila/ui/index.html
#         ${CMAKE_CURRENT_SOURCE_DIR}/../sila/ui/app.js
#         ${CMAKE_CURRENT_SOURCE_DIR}/webui/bridge.js
# )

target_sources(SILA PRIVATE
    src/PluginProcessor.cpp
    src/engine/Sampler.cpp       # Phase 2
    src/engine/VoiceMixer.cpp    # Phase 2
    # added as they are ported (see DESIGN.md roadmap):
    # src/PluginEditor.cpp       # Phase 4 (WebView editor)
    # src/engine/Sequencer.cpp   # Phase 3
    # src/engine/Fx.cpp          # Phase 5
)

target_include_directories(SILA PRIVATE src)

target_compile_definitions(SILA PUBLIC
    JUCE_WEB_BROWSER=0
    JUCE_USE_CURL=0
    JUCE_VST3_CAN_REPLACE_VST2=0
)

target_link_libraries(SILA PRIVATE
    juce::juce_audio_utils
    juce::juce_dsp
    PUBLIC
    juce::juce_recommended_config_flags
    juce::juce_recommended_lto_flags
    juce::juce_recommended_warning_flags
)
