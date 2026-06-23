// SILA unit tests — the security backstop.
//
// These guard the pure functions that stand between a user/shared-project string
// and the filesystem: the path-traversal check (safeChild) and the filename/pack
// sanitizers. They're the only thing stopping a crafted name from making a
// library delete/move/rename escape ~/SILA/library, so a regression here must
// fail the build, not ship silently. No audio device / host needed.
//
// Run:  cmake --build vst/build --target SILA_Tests
//       "vst/build/SILA_Tests_artefacts/Debug/SILA Tests.exe"

#include "engine/Library.h"
#include <juce_core/juce_core.h>
#include <iostream>

using namespace sila::engine;

static int g_checks = 0, g_fails = 0;

static void check (bool cond, const juce::String& msg)
{
    ++g_checks;
    if (! cond) { ++g_fails; std::cerr << "  FAIL: " << msg << std::endl; }
}

int main()
{
    // base need not exist — safeChild is pure path math (getFullPathName + isAChildOf).
    const juce::File base = juce::File::getSpecialLocation (juce::File::tempDirectory)
                                .getChildFile ("SILA_test_library_root");

    auto allowed = [&] (const char* rel) { bool ok = false; safeChild (base, rel, ok); return ok; };

    std::cout << "safeChild — path-traversal guard" << std::endl;
    check ( allowed ("kick.wav"),            "plain child allowed");
    check ( allowed ("01. Kick/kick.wav"),   "nested child allowed");
    check ( allowed ("sub/../kick.wav"),     "dotdot that stays inside is allowed");
    check (! allowed ("../escape.wav"),      "../ escape blocked");
    check (! allowed ("../../secret"),       "../../ escape blocked");
    check (! allowed ("a/../../b"),          "embedded ../.. escape blocked");
    check (! allowed ("..\\win"),            "backslash .. escape blocked");
    check (! allowed ("C:/Windows/System32"),"absolute drive path blocked");
    check (! allowed ("\\\\server\\share"),  "UNC path blocked");
    {
        bool ok = false;
        const auto f = safeChild (base, "", ok);
        check (ok && f == base, "empty path resolves to base (callers must guard the root itself)");
    }

    std::cout << "sanitizeLibraryFilename" << std::endl;
    check ( sanitizeLibraryFilename ("kick.wav")    == "kick.wav",    "clean name unchanged");
    check ( sanitizeLibraryFilename ("my kick.wav") == "my_kick.wav", "spaces -> underscore");
    check ( sanitizeLibraryFilename (".hidden.wav") == "hidden.wav",  "leading dot stripped");
    {
        const auto r = sanitizeLibraryFilename ("../../etc/passwd.wav");
        check (! r.containsChar ('/') && ! r.containsChar ('\\') && ! r.contains (".."),
               "traversal chars stripped from filename (got: " + r + ")");
    }
    check ( sanitizeLibraryFilename ("$$$.wav") == "sample.wav", "all-invalid stem -> 'sample'");
    {
        const auto r = sanitizeLibraryFilename (juce::String::repeatedString ("a", 100) + ".wav");
        check (r.length() <= 64 + 4 && r.endsWith (".wav"), "stem capped to 64, extension kept");
    }

    std::cout << "sanitizePackName" << std::endl;
    check ( sanitizePackName ("My Pack")   == "My_Pack",   "spaces -> underscore");
    check ( sanitizePackName ("Drums 808") == "Drums_808", "alnum kept");
    {
        const auto r = sanitizePackName ("../evil");
        check (! r.containsChar ('/') && ! r.contains (".."), "traversal stripped from pack name (got: " + r + ")");
    }
    check ( sanitizePackName ("CON").isEmpty(),     "reserved 'CON' blocked");
    check ( sanitizePackName ("con").isEmpty(),     "reserved name is case-insensitive");
    check ( sanitizePackName ("NUL.wav").isEmpty(), "reserved 'NUL.*' blocked");
    check ( sanitizePackName ("COM1").isEmpty(),    "reserved 'COM1' blocked");
    check ( sanitizePackName ("...").isEmpty(),     "all-dots -> empty");

    std::cout << "isLibraryAudioFile" << std::endl;
    check ( isLibraryAudioFile (base.getChildFile ("y.wav")),  ".wav accepted");
    check ( isLibraryAudioFile (base.getChildFile ("y.AIFF")), ".AIFF accepted (case-insensitive)");
    check (! isLibraryAudioFile (base.getChildFile ("y.mp3")), ".mp3 rejected");
    check (! isLibraryAudioFile (base.getChildFile ("y.exe")), ".exe rejected");

    std::cout << std::endl;
    if (g_fails == 0)
    {
        std::cout << "ALL PASS (" << g_checks << " checks)" << std::endl;
        return 0;
    }
    std::cerr << g_fails << " / " << g_checks << " checks FAILED" << std::endl;
    return 1;
}
