#pragma once
#include <juce_audio_basics/juce_audio_basics.h>
#include "engine/Project.h"   // FilterMode (the SVF output tap)
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

// Self-contained Trapezoidal TPT (zero-delay-feedback) state-variable filter
// (Andrew Simper / Cytomic). bake() is called once per trigger (and re-baked at
// LFO control rate); only process() runs per sample — ~10 mul/add, unconditionally
// stable at any cutoff/Q. The same integrator state yields LP/HP/BP via `mode`.
struct TptSvf
{
    FilterMode mode = FilterMode::LowPass;            // output tap
    float a1 = 0.0f, a2 = 0.0f, a3 = 0.0f, k = 0.0f;  // baked coeffs
    float ic1eq = 0.0f, ic2eq = 0.0f;                 // integrator state

    // Compute coeffs from cutoff (0..1, log-mapped 20 Hz..~open) and resonance
    // (0..1 -> Q 0.5..20). Mode-independent — the tap is chosen in process().
    void bake (float cutoff, float resonance, double sampleRate);

    // One sample: advance the two integrators, return the selected tap.
    float process (float x)
    {
        const float v3 = x - ic2eq;
        const float v1 = a1 * ic1eq + a2 * v3;
        const float v2 = ic2eq + a2 * ic1eq + a3 * v3;
        ic1eq = 2.0f * v1 - ic1eq;
        ic2eq = 2.0f * v2 - ic2eq;
        switch (mode)
        {
            case FilterMode::HighPass: return x - k * v1 - v2;
            case FilterMode::BandPass: return v1;
            case FilterMode::LowPass:  break;
        }
        return v2;
    }
};

// Per-track mixer params, recomputed each block from the snapshot and looked up
// by Voice.trackIndex — so volume/pan apply CONTINUOUSLY (a fader/pan move
// affects voices already ringing), not baked at trigger time.
struct TrackMix
{
    float gain = 1.0f;
    float panL = 0.70710678f, panR = 0.70710678f;   // equal-power (constant power)
};

// One per-track (aux) output target for multi-out: raw stereo channel pointers
// into the host's process buffer. L == nullptr means that lane's bus is disabled,
// so its voices only sum into the Main mix. Raw pointers (not an AudioBuffer) so
// the processor can hand the mixer views into the bus channels without copying.
struct LaneOut
{
    float* L = nullptr;
    float* R = nullptr;
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

    // Per-voice TPT state-variable filter. Coeffs baked at trigger from the
    // (p-locked) cutoff/resonance; only the integrator ticks per sample.
    // filterOn=false => bypassed (zero cost). Own state => the tail rings at this
    // voice's cutoff even when a later step opens a brighter voice.
    bool   filterOn = false;
    TptSvf svf;

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

    // Hard-capped at kMaxVoices: at the cap, steal the longest-ringing voice
    // (max `elapsed` — a not-yet-started deferred voice has elapsed 0 and is
    // never stolen). This bounds worst-case CPU and guarantees push_back never
    // reallocates on the audio thread. The steal is a hard cut; at a 512-voice
    // density the dropped tail is masked by the mix.
    void addVoice (const Voice& v)
    {
        if ((int) voices.size() >= kMaxVoices)
        {
            size_t victim = 0;
            for (size_t i = 1; i < voices.size(); ++i)
                if (voices[i].elapsed > voices[victim].elapsed)
                    victim = i;
            voices[victim] = v;
            return;
        }
        voices.push_back (v);
    }
    int  activeVoiceCount() const { return (int) voices.size(); }

    // `trackMix` is indexed by Voice.trackIndex (per-track gain + equal-power pan).
    // `block` is the Main bus (the full summed mix). If `lanes` is non-null, each
    // voice is ALSO written to lanes[trackIndex] (its per-track multi-out bus, pre-
    // master) in the SAME pass, so the per-lane stems sum to the Main mix exactly.
    // numLanes bounds the lookup; a voice with no matching lane (e.g. an audition,
    // trackIndex < 0) goes to Main only.
    void renderInto (juce::AudioBuffer<float>& block, const std::vector<TrackMix>& trackMix,
                     const LaneOut* lanes = nullptr, int numLanes = 0);
    void applyMaster (juce::AudioBuffer<float>& block, bool smallSpeaker, float masterVol);

private:
    static float softClip (float x, float knee);

    // Control-rate LFO update for one voice: eval shape, apply to its destination
    // (recompute SVF coeffs / set gain / set rate), advance phase a control block.
    void updateVoiceLfo (Voice& v);

    // Audio-thread voice pool. reserve()d in prepare() so steady-state addVoice()
    // never reallocates; a transient burst past this still grows correctly.
    static constexpr int kMaxVoices = 512;
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
