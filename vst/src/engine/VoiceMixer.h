#pragma once
#include <juce_audio_basics/juce_audio_basics.h>
#include <vector>
#include <memory>

// Port of ../../sila/engine/audio.py (the mixing half — the device/stream/
// watcher half is dropped; the host owns the device).
//
// renderInto() mixes all active voices into the block (port of
// AudioEngine._callback); applyMaster() does the master stage — hard clip by
// default, or the opt-in small-speaker monitor (the HPF + psychoacoustic-bass
// harmonics + soft-limit we ported to JS in app.js).
namespace sila::engine
{
struct Voice
{
    const juce::AudioBuffer<float>* audio = nullptr;  // mono source
    double pos  = 0.0;                                 // fractional read position (varispeed pitch)
    int   endPos = 0;                                 // stop index (slice end, in source samples)
    double rate  = 1.0;                                // playback rate = 2^(pitch_offset/12)
    float volume = 1.0f;
    float panL = 0.70710678f, panR = 0.70710678f;
    int   startOffset = 0;   // samples to wait before first output (was delay_frames)

    // Note-length gate (output samples): <= 0 = one-shot (play the whole sample);
    // > 0 = hold this many output samples, then release. `elapsed` counts output
    // samples actually rendered, so the gate is pitch-independent.
    int   gateSamples = 0;
    int   elapsed     = 0;

    // Pins the buffer's owner (the Sampler) alive for the voice's lifetime, so an
    // RCU bank swap that retires+frees that sampler can't dangle `audio` while the
    // voice is still ringing. Type-erased to keep the mixer free of a Sampler dep.
    std::shared_ptr<const void> keepAlive;
};

class VoiceMixer
{
public:
    void prepare (double sr);
    void reset();

    void addVoice (const Voice& v) { voices.push_back (v); }
    int  activeVoiceCount() const { return (int) voices.size(); }

    void renderInto (juce::AudioBuffer<float>& block);
    void applyMaster (juce::AudioBuffer<float>& block, bool smallSpeaker, float masterVol);

private:
    static float softClip (float x, float knee);

    std::vector<Voice> voices;
    double sampleRate { 48000.0 };

    // Click-free gate edges (set in prepare): ~1 ms attack de-click, ~8 ms
    // linear release. Short enough to preserve drum transients.
    int envAttack  { 48 };
    int envRelease { 384 };

    // Small-speaker one-pole filter state, per channel [L, R].
    double ssSub[2]  { 0.0, 0.0 };
    double ssLow[2]  { 0.0, 0.0 };
    double ssHarm[2] { 0.0, 0.0 };
};
} // namespace sila::engine
