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
struct Voice;   // fwd

// Compute + store the TPT-SVF coefficients (a1/a2/a3 + k) on a voice from cutoff
// (0..1) / resonance (0..1). Mode-independent — the output tap (LP/HP/BP) is
// chosen at render. Shared by the trigger bake and the LFO control-rate update.
void bakeSvf (Voice& v, float cutoff, float resonance, double sampleRate);

// Per-track mixer params, recomputed each block from the snapshot and looked up
// by Voice.trackIndex — so volume/pan apply CONTINUOUSLY (a fader/pan move
// affects voices already ringing), not baked at trigger time.
struct TrackMix
{
    float gain = 1.0f;
    float panL = 0.70710678f, panR = 0.70710678f;   // equal-power (constant power)
};

struct Voice
{
    const juce::AudioBuffer<float>* audio = nullptr;  // mono source
    double pos  = 0.0;                                 // fractional read position (varispeed pitch)
    int   endPos = 0;                                 // stop index (slice end, in source samples)
    double rate  = 1.0;                                // playback rate = 2^(pitch_offset/12)
    float volume = 1.0f;                               // per-hit (velocity), baked at trigger
    int   trackIndex = 0;                              // selects per-track gain/pan (continuous)
    int   startOffset = 0;   // samples to wait before first output (was delay_frames)

    // Note-length gate (output samples): <= 0 = one-shot (play the whole sample);
    // > 0 = hold this many output samples, then release. `elapsed` counts output
    // samples actually rendered, so the gate is pitch-independent.
    int   gateSamples = 0;
    int   elapsed     = 0;

    // Per-voice TPT (zero-delay-feedback) state-variable lowpass. Coeffs baked at
    // trigger from the (p-locked) cutoff/resonance; only the integrator ticks per
    // sample. filterOn=false => bypassed (zero cost). Own state => the tail rings
    // at this voice's cutoff even when a later step opens a brighter voice.
    bool  filterOn = false;
    int   filterMode = 0;     // FilterMode (LP/HP/BP) — selects the SVF output tap
    float svfA1 = 0.0f, svfA2 = 0.0f, svfA3 = 0.0f, svfK = 0.0f;
    float ic1eq = 0.0f, ic2eq = 0.0f;

    // Base (pre-LFO) values the LFO retargets at control rate (volume/rate also
    // double as the live values when the LFO doesn't target them).
    float  baseCutoff = 1.0f, baseResonance = 0.0f, baseGain = 1.0f;
    double baseRate = 1.0;

    // Per-voice LFO. on=false => no modulation (zero cost). phase advances inc
    // per sample, evaluated/applied in 32-sample control blocks (ctr). shVal is
    // the held sample-and-hold value for the `random` shape.
    struct LFO
    {
        bool   on = false;
        int    shape = 0;     // LfoShape
        int    dest = 0;      // LfoDest
        double phase = 0.0;
        double inc = 0.0;     // 2π·rate/sr
        float  depth = 0.0f;
        int    ctr = 0;
        float  shVal = 0.0f;
    } lfo;

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

    // `trackMix` is indexed by Voice.trackIndex (per-track gain + equal-power pan).
    void renderInto (juce::AudioBuffer<float>& block, const std::vector<TrackMix>& trackMix);
    void applyMaster (juce::AudioBuffer<float>& block, bool smallSpeaker, float masterVol);

private:
    static float softClip (float x, float knee);

    // Control-rate LFO update for one voice: eval shape, apply to its destination
    // (recompute SVF coeffs / set gain / set rate), advance phase a control block.
    void updateVoiceLfo (Voice& v);

    std::vector<Voice> voices;
    double sampleRate { 48000.0 };
    juce::Random rng;                  // sample-and-hold random source (audio thread only)

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
