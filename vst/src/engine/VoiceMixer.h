#pragma once
#include <juce_audio_basics/juce_audio_basics.h>
#include <vector>

// Port of ../../sila/engine/audio.py (the mixing half — the device/stream/
// watcher half is dropped; the host owns the device).
//
// A Voice is a playing sample with volume/pan/start-offset; renderInto() mixes
// all active voices into the block (port of AudioEngine._callback), then
// applyMaster() does the soft-clip / optional small-speaker monitor stage
// (the _soft_clip + _apply_small_speaker code we wrote).
namespace sila::engine
{
struct Voice
{
    const juce::AudioBuffer<float>* audio = nullptr;
    int   pos          = 0;
    float volume       = 1.0f;
    float panL = 0.7071f, panR = 0.7071f;
    int   startOffset  = 0;   // sample offset within the first block (was delay_frames)
    int   framesLeft   = -1;  // -1 = play to end (was frames_remaining)
};

class VoiceMixer
{
public:
    void prepare (double sampleRate);
    void addVoice (Voice v);                          // play()
    void renderInto (juce::AudioBuffer<float>& block); // _callback mixing loop
    void applyMaster (juce::AudioBuffer<float>& block, bool smallSpeaker, float masterVol);

private:
    std::vector<Voice> voices;
    double sampleRate { 48000.0 };
    // small-speaker filter state (the vectorised one-pole we ported to JS),
    // here a couple of juce::dsp::IIR / manual one-pole states per channel.
};
} // namespace sila::engine
