#include "PluginProcessor.h"
#include "PluginEditor.h"
#include <cmath>
#include <algorithm>

// ---------------------------------------------------------------------------
// Bring-up tracer. Writes a flushed line to "SILA_trace.log" on the Desktop so
// we can see exactly how far the Standalone gets before a silent exit/crash.
// Each call appends + flushes immediately, so the last line in the file is the
// last point we reached. Remove this block (and the SILA_TRACE calls) once the
// startup path is proven.
namespace
{
    void silaTrace (const juce::String& msg)
    {
        static juce::FileLogger logger (
            juce::File::getSpecialLocation (juce::File::userDesktopDirectory)
                .getChildFile ("SILA_trace.log"),
            "SILA standalone startup trace");
        logger.logMessage (msg);
    }
}
#define SILA_TRACE(msg) silaTrace (msg)

SilaAudioProcessor::SilaAudioProcessor()
    : juce::AudioProcessor (BusesProperties()
          .withOutput ("Output", juce::AudioChannelSet::stereo(), true)),
      apvts (*this, nullptr, "SILA", makeParameters())
{
    SILA_TRACE ("ctor: body entered (apvts constructed OK)");
}

SilaAudioProcessor::~SilaAudioProcessor() = default;

juce::AudioProcessorValueTreeState::ParameterLayout SilaAudioProcessor::makeParameters()
{
    SILA_TRACE ("makeParameters: building layout (runs during apvts construction)");
    using namespace juce;
    AudioProcessorValueTreeState::ParameterLayout layout;
    layout.add (std::make_unique<AudioParameterFloat>(
        ParameterID { "masterVol", 1 }, "Master Volume", 0.0f, 2.0f, 1.0f));
    layout.add (std::make_unique<AudioParameterFloat>(
        ParameterID { "swing", 1 }, "Swing", 0.0f, 1.0f, 0.0f));
    layout.add (std::make_unique<AudioParameterBool>(
        ParameterID { "smallSpeaker", 1 }, "Small-Speaker Monitor", false));
    layout.add (std::make_unique<AudioParameterBool>(
        ParameterID { "songMode", 1 }, "Song Mode", true));
    SILA_TRACE ("makeParameters: layout built (4 params)");
    return layout;
}

// A short percussive kick: pitch-dropping sine with an exponential envelope.
juce::AudioBuffer<float> SilaAudioProcessor::makeKick (double sr)
{
    const double dur = 0.18;
    const int n = juce::jmax (1, (int) (sr * dur));
    juce::AudioBuffer<float> b (1, n);
    auto* d = b.getWritePointer (0);
    double phase = 0.0;
    for (int i = 0; i < n; ++i)
    {
        const double t = i / sr;
        const double f = 45.0 + 120.0 * std::exp (-t * 30.0);   // 165 Hz → 45 Hz
        phase += 2.0 * juce::MathConstants<double>::pi * f / sr;
        const double env = std::exp (-t * 12.0);
        d[i] = (float) (std::sin (phase) * env * 0.9);
    }
    return b;
}

// A snare: a short tone plus a noise burst under a fast exponential envelope.
juce::AudioBuffer<float> SilaAudioProcessor::makeSnare (double sr)
{
    const double dur = 0.20;
    const int n = juce::jmax (1, (int) (sr * dur));
    juce::AudioBuffer<float> b (1, n);
    auto* d = b.getWritePointer (0);
    juce::Random r;
    double phase = 0.0;
    for (int i = 0; i < n; ++i)
    {
        const double t = i / sr;
        const double env = std::exp (-t * 22.0);
        phase += 2.0 * juce::MathConstants<double>::pi * 180.0 / sr;
        const double tone  = std::sin (phase);
        const double noise = r.nextFloat() * 2.0 - 1.0;
        d[i] = (float) ((0.5 * tone + 0.8 * noise) * env * 0.7);
    }
    return b;
}

// A closed hi-hat: high-passed noise under a very fast decay.
juce::AudioBuffer<float> SilaAudioProcessor::makeHat (double sr)
{
    const double dur = 0.05;
    const int n = juce::jmax (1, (int) (sr * dur));
    juce::AudioBuffer<float> b (1, n);
    auto* d = b.getWritePointer (0);
    juce::Random r;
    double prev = 0.0;
    for (int i = 0; i < n; ++i)
    {
        const double t = i / sr;
        const double env = std::exp (-t * 80.0);
        const double noise = r.nextFloat() * 2.0 - 1.0;
        const double hp = noise - prev;     // crude one-pole high-pass for a thin "tss"
        prev = noise;
        d[i] = (float) (hp * env * 0.5);
    }
    return b;
}

void SilaAudioProcessor::prepareToPlay (double sr, int samplesPerBlock)
{
    SILA_TRACE ("prepareToPlay: enter sr=" + juce::String (sr)
                + " block=" + juce::String (samplesPerBlock));
    sampleRate = sr;
    internalPpq = 0.0;
    lastFiredSixteenth = -1;

    auto initial = buildDemoProject (sr);
    const int trackCount = (int) initial->tracks.size();
    liveProject.store (std::move (initial), std::memory_order_release);

    mixer.prepare (sr);
    SILA_TRACE ("prepareToPlay: exit (" + juce::String (trackCount)
                + " tracks, samplers + mixer ready)");
}

void SilaAudioProcessor::reapRetired()
{
    // Message thread: free snapshots no audio-thread reader still holds (only
    // this retire list references them => use_count() == 1).
    retiredProjects.erase (
        std::remove_if (retiredProjects.begin(), retiredProjects.end(),
                        [] (const ProjectPtr& p) { return p.use_count() <= 1; }),
        retiredProjects.end());
}

// Phase 3: until the UI bridge (Phase 4) authors patterns, build a small demo
// project in code so the new Sequencer features are audible in the Standalone:
//  - kick  : 4-on-the-floor
//  - snare : beats 2 & 4, plus a ghost on step 14 with a 1:2 trig condition
//  - hats  : offbeats, with a probability step and a micro-timed (late) step
// Swing is driven live from the "swing" APVTS param (see processBlock).
SilaAudioProcessor::ProjectPtr SilaAudioProcessor::buildDemoProject (double sr)
{
    using namespace sila::engine;

    auto proj = std::make_shared<Project>();
    samplers.clear();

    auto addTrack = [&] (const juce::String& name, juce::AudioBuffer<float> sample) -> Track&
    {
        Track t;
        t.id   = name;
        t.name = name;
        t.steps.resize (16);            // 16-step loop
        proj->tracks.push_back (std::move (t));

        samplers.push_back (std::make_unique<Sampler>());
        Sampler& smp = *samplers.back();
        smp.prepare (sr);
        smp.addBuffer (std::move (sample));
        return proj->tracks.back();
    };

    // Kick — 4-on-the-floor.
    {
        Track& k = addTrack ("Kick", makeKick (sr));
        for (int i = 0; i < 16; i += 4)
        {
            k.steps[(size_t) i].active   = true;
            k.steps[(size_t) i].velocity = 110;
        }
    }

    // Snare — beats 2 & 4, plus a 1:2 ghost on step 14.
    {
        Track& s = addTrack ("Snare", makeSnare (sr));
        for (int i : { 4, 12 })
        {
            s.steps[(size_t) i].active   = true;
            s.steps[(size_t) i].velocity = 100;
        }
        Step& ghost = s.steps[14];
        ghost.active   = true;
        ghost.velocity = 70;
        ghost.trig     = TrigCondition::OneIn2;   // fires every other loop
    }

    // Hats — offbeats, with a 50% step and a micro-timed (late) step.
    {
        Track& h = addTrack ("Hat", makeHat (sr));
        for (int i : { 2, 6, 10, 14 })
        {
            h.steps[(size_t) i].active   = true;
            h.steps[(size_t) i].velocity = 80;
        }
        h.steps[6].microTiming = 12;     // pushed late (clock.py: micro * interval / 6)
        h.steps[10].probability = 50;    // fires ~half the time
    }

    // Phase 3b — song mode. Author a pattern bank + chain so bar-by-bar swaps
    // are audible without a UI. Each slot is parallel to project.tracks
    // (Kick=0, Snare=1, Hat=2). Slot 0 is left empty → the Sequencer falls back
    // to the live base groove above, so chain entry 0 == the Phase 3a pattern.
    auto steps16 = [] (std::initializer_list<int> on, int velocity)
    {
        std::vector<Step> v (16);
        for (int i : on)
        {
            v[(size_t) i].active   = true;
            v[(size_t) i].velocity = velocity;
        }
        return v;
    };

    auto& bank = proj->patternBank;
    // Slot 1 — variation: a kick pickup and straight-8th hats.
    bank.slots[1] = {
        steps16 ({ 0, 4, 8, 12, 14 }, 110),                 // kick + pickup on the "a" of 4
        steps16 ({ 4, 12 }, 100),                            // backbeat snare
        steps16 ({ 0, 2, 4, 6, 8, 10, 12, 14 }, 75),         // driving 8th-note hats
    };
    // Slot 2 — fill: a snare roll into the turnaround.
    bank.slots[2] = {
        steps16 ({ 0, 8 }, 110),                             // sparse kick
        steps16 ({ 8, 10, 12, 13, 14, 15 }, 95),             // snare roll
        steps16 ({ 2, 6, 10, 14 }, 80),                      // offbeat hats
    };

    proj->songChain = { 0, 1, 0, 2 };    // A B A C, one bar each (active slot gated by songMode param)
    return proj;
}

void SilaAudioProcessor::processBlock (juce::AudioBuffer<float>& buffer,
                                       juce::MidiBuffer& /*midi*/)
{
    juce::ScopedNoDenormals noDenormals;
    buffer.clear();

    static bool firstBlock = true;
    if (firstBlock)
    {
        firstBlock = false;
        SILA_TRACE ("processBlock: first call ch=" + juce::String (buffer.getNumChannels())
                    + " n=" + juce::String (buffer.getNumSamples())
                    + " wrapper=" + juce::String ((int) wrapperType));
    }

    const int numSamples = buffer.getNumSamples();

    // --- Resolve transport: host if playing, else internal (Standalone only) --
    double bpm = kDefaultBpm, ppqStart = internalPpq;
    bool playing = false;

    if (auto* ph = getPlayHead())
    {
        if (auto pos = ph->getPosition())
        {
            if (pos->getIsPlaying())
            {
                playing  = true;
                bpm      = pos->getBpm().orFallback (kDefaultBpm);
                ppqStart = pos->getPpqPosition().orFallback (internalPpq);
            }
        }
    }

    if (! playing && wrapperType == wrapperType_Standalone)
    {
        playing  = true;            // free-run so the Standalone app makes sound
        bpm      = kDefaultBpm;
        ppqStart = internalPpq;
    }

    // One atomic load of the immutable snapshot for the whole block (RCU read).
    const ProjectPtr proj = snapshot();

    // Live performance scalars from the automatable params / atomics.
    const auto* swParam = apvts.getRawParameterValue ("swing");
    const auto* smParam = apvts.getRawParameterValue ("songMode");
    const float swing      = swParam != nullptr ? swParam->load() : 0.0f;
    const bool  songMode   = smParam != nullptr && smParam->load() > 0.5f;
    const bool  fill       = fillActive.load (std::memory_order_relaxed);

    // Publish the transport position + status for the editor (C++ -> UI). The
    // active song slot is a pure function of position (-1 when off/stopped).
    currentPpq.store (ppqStart, std::memory_order_relaxed);
    transportPlaying.store (playing, std::memory_order_relaxed);
    currentBpm.store (bpm, std::memory_order_relaxed);
    {
        const long absSixteenth = (long) std::floor (ppqStart * 4.0 + 1e-9);
        const int  slot = (proj != nullptr && playing && songMode)
                            ? sequencer.resolveSongSlot (*proj, absSixteenth, songMode) : -1;
        currentSongSlot.store (slot, std::memory_order_relaxed);
    }

    if (proj != nullptr && playing && bpm > 0.0)
    {
        scheduleTriggers (*proj, ppqStart, bpm, numSamples, swing, songMode, fill);
        // Advance the internal clock to the end of this block (keeps it in sync
        // with the host when host-driven; carries the free-run when not).
        const double blockQuarters = numSamples * (bpm / 60.0) / sampleRate;
        internalPpq = ppqStart + blockQuarters;
    }

    // Tails keep ringing even when stopped, so always render + master.
    mixer.renderInto (buffer);

    const auto* masterVolParam   = apvts.getRawParameterValue ("masterVol");
    const auto* smallSpeakerParam = apvts.getRawParameterValue ("smallSpeaker");
    jassert (masterVolParam != nullptr && smallSpeakerParam != nullptr);
    const float masterVol    = masterVolParam   != nullptr ? masterVolParam->load() : 1.0f;
    const bool  smallSpeaker = smallSpeakerParam != nullptr && smallSpeakerParam->load() > 0.5f;
    mixer.applyMaster (buffer, smallSpeaker, masterVol);
}

void SilaAudioProcessor::scheduleTriggers (const sila::engine::Project& proj,
                                           double ppqStart, double bpm, int numSamples,
                                           float swing, bool songMode, bool fill)
{
    // 1 quarter note = 4 sixteenths; ppq is in quarter notes.
    const double sixteenthStart = ppqStart * 4.0;
    const double samplesPer16   = sampleRate * 60.0 / bpm / 4.0;

    // If the transport jumped backwards (loop/relocate), re-arm.
    if (sixteenthStart + 1e-6 < (double) lastFiredSixteenth)
        lastFiredSixteenth = (long) std::floor (sixteenthStart) - 1;

    // Swing (port of clock.py): odd-indexed 16ths shift by swing * interval / 2.
    const double swingOffset = (double) swing * samplesPer16 * 0.5;

    long idx = (long) std::ceil (sixteenthStart - 1e-9);
    for (;;)
    {
        const double offset = (idx - sixteenthStart) * samplesPer16;
        if (offset >= numSamples)
            break;
        if (offset >= 0.0 && idx > lastFiredSixteenth)
        {
            lastFiredSixteenth = idx;
            const long absIdx = idx;

            sequencer.forEachTrig (proj, absIdx, songMode, fill, [&] (const sila::engine::TrigEvent& ev)
            {
                if (ev.trackIndex < 0 || ev.trackIndex >= (int) samplers.size())
                    return;

                const float s = ev.pStart.has_value() ? *ev.pStart : -1.0f;
                const float e = ev.pEnd.has_value()   ? *ev.pEnd   : -1.0f;
                const auto slice = samplers[(size_t) ev.trackIndex]->get (ev.velocity, s, e);
                if (slice.buffer == nullptr)
                    return;

                // clock.py timing: odd 16ths swung; micro-timing late only (the
                // sleep-loop original clamps negative offsets to 0). VoiceMixer
                // defers any startOffset >= numSamples to the next block, so a
                // positive micro-timed voice near the block edge stays accurate.
                double extra = (absIdx & 1) ? -swingOffset : 0.0;
                const double mt = ev.microTiming * samplesPer16 / 6.0;
                if (mt > 0.0)
                    extra += mt;

                int startOffset = (int) std::lround (offset + extra);
                if (startOffset < 0)
                    startOffset = 0;     // can't render in the past (clamp at block edge)

                sila::engine::Voice v;
                v.audio       = slice.buffer;
                v.pos         = slice.start;
                v.endPos      = slice.start + slice.length;
                v.startOffset = startOffset;
                v.volume      = juce::jlimit (0.0f, 1.0f, (float) ev.velocity / 127.0f);
                v.panL = v.panR = 0.70710678f;     // centre (per-track pan is Phase 5)
                mixer.addVoice (v);
            });
        }
        ++idx;
    }
}

void SilaAudioProcessor::getStateInformation (juce::MemoryBlock& dest)
{
    if (auto xml = apvts.copyState().createXml())
        copyXmlToBinary (*xml, dest);
}

void SilaAudioProcessor::setStateInformation (const void* data, int sizeInBytes)
{
    if (auto xml = getXmlFromBinary (data, sizeInBytes))
        apvts.replaceState (juce::ValueTree::fromXml (*xml));
}

juce::AudioProcessorEditor* SilaAudioProcessor::createEditor()
{
    // Phase 4: the WebView editor hosts the vanilla HTML/JS UI + native bridge.
    SILA_TRACE ("createEditor: creating SilaAudioProcessorEditor (WebView)");
    auto* ed = new SilaAudioProcessorEditor (*this);
    SILA_TRACE ("createEditor: done");
    return ed;
}

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    SILA_TRACE ("createPluginFilter: instantiating SilaAudioProcessor");
    return new SilaAudioProcessor();
}
