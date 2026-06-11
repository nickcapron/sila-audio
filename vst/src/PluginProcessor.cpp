#include "PluginProcessor.h"
#include "PluginEditor.h"
#include "engine/ProjectJson.h"
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

    if (liveProject.load (std::memory_order_acquire) == nullptr)
    {
        // First prepare: author the in-code demo project + its sampler bank.
        liveProject.store (buildDemoProject (sr), std::memory_order_release);
    }
    else
    {
        // Re-prepare (device rate / block-size change): keep the project + user
        // edits, just re-resample the file-backed samplers to the new rate.
        rebuildSamplerBankForRate (sr);
    }

    mixer.prepare (sr);
    const auto live = liveProject.load (std::memory_order_acquire);
    const int trackCount = live != nullptr ? (int) live->tracks.size() : 0;
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

    // Same reclamation for superseded sampler banks (audio thread done with them).
    retiredSamplers.erase (
        std::remove_if (retiredSamplers.begin(), retiredSamplers.end(),
                        [] (const SamplerBankPtr& b) { return b.use_count() <= 1; }),
        retiredSamplers.end());
}

juce::File SilaAudioProcessor::libraryRoot()
{
    return juce::File::getSpecialLocation (juce::File::userHomeDirectory)
               .getChildFile ("SILA").getChildFile ("library");
}

std::shared_ptr<sila::engine::Sampler>
SilaAudioProcessor::buildSamplerFromLayers (const std::vector<sila::engine::SampleRef>& layers, double sr)
{
    auto smp = std::make_shared<sila::engine::Sampler>();
    smp->prepare (sr);
    for (const auto& layer : layers)
    {
        juce::File f (layer.path);
        if (! f.existsAsFile())
            f = libraryRoot().getChildFile (layer.path);   // resolve library-relative
        if (f.existsAsFile())
            smp->addFile (f, layer.velMin, layer.velMax, layer.rrGroup, layer.start, layer.end);
    }
    return smp;
}

void SilaAudioProcessor::assignTrackSamples (int trackIndex,
                                             const std::vector<sila::engine::SampleRef>& layers)
{
    auto cur = liveSamplers.load (std::memory_order_acquire);
    if (cur == nullptr || trackIndex < 0 || trackIndex >= (int) cur->size())
        return;

    // Build the replacement sampler (resamples files to the device rate), then
    // copy the bank (other tracks keep their sampler + RR state), swap this one.
    auto next = std::make_shared<SamplerBank> (*cur);
    (*next)[(size_t) trackIndex] = buildSamplerFromLayers (layers, sampleRate);
    auto old = liveSamplers.exchange (std::make_shared<const SamplerBank> (std::move (*next)),
                                      std::memory_order_acq_rel);
    if (old) retiredSamplers.push_back (std::move (old));
}

void SilaAudioProcessor::rebuildSamplerBankForRate (double sr)
{
    auto proj = liveProject.load (std::memory_order_acquire);
    auto cur  = liveSamplers.load (std::memory_order_acquire);
    if (proj == nullptr)
        return;

    auto next = std::make_shared<SamplerBank>();
    next->resize (proj->tracks.size());
    for (size_t i = 0; i < proj->tracks.size(); ++i)
    {
        const auto& layers = proj->tracks[i].samples;
        if (! layers.empty())
            (*next)[i] = buildSamplerFromLayers (layers, sr);     // re-resample at the new rate
        else if (cur != nullptr && i < cur->size())
            (*next)[i] = (*cur)[i];   // transitional synth kit: keep (built at the old rate)
    }

    // prepareToPlay is not concurrent with processBlock, so the superseded bank
    // has no audio-thread reader — store lets it free here, no retire list needed.
    liveSamplers.store (std::make_shared<const SamplerBank> (std::move (*next)),
                        std::memory_order_release);
}

SilaAudioProcessor::SamplerBank
SilaAudioProcessor::buildBankForProject (const sila::engine::Project& proj, double sr)
{
    SamplerBank bank;
    bank.reserve (proj.tracks.size());
    for (const auto& t : proj.tracks)
        bank.push_back (buildSamplerFromLayers (t.samples, sr));   // empty layers => silent sampler
    return bank;
}

void SilaAudioProcessor::setProject (ProjectPtr proj, SamplerBankPtr bank)
{
    // The audio thread may be reading the current snapshot+bank, so publish
    // atomically and retire the old ones (freed later by reapRetired). Publish
    // the bank first: for the one block where track count and bank size may
    // disagree, forEachTrig's bounds check simply skips — no glitch.
    if (auto oldBank = liveSamplers.exchange (bank, std::memory_order_acq_rel))
        retiredSamplers.push_back (std::move (oldBank));
    if (auto oldProj = liveProject.exchange (proj, std::memory_order_acq_rel))
        retiredProjects.push_back (std::move (oldProj));

    projectEpoch.fetch_add (1, std::memory_order_release);   // tell the editor to refresh
    reapRetired();   // message thread; keeps the retire lists from growing on reload
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
    auto samplerBank = std::make_shared<SamplerBank>();

    auto addTrack = [&] (const juce::String& name, juce::AudioBuffer<float> sample) -> Track&
    {
        Track t;
        t.id   = name;
        t.name = name;
        t.steps.resize (16);            // 16-step loop
        proj->tracks.push_back (std::move (t));

        auto smp = std::make_shared<Sampler>();
        smp->prepare (sr);
        smp->addBuffer (std::move (sample));   // synthesized demo kit (no file path)
        samplerBank->push_back (std::move (smp));
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

    // Publish the matching sampler bank (parallel to proj->tracks by index).
    liveSamplers.store (std::make_shared<const SamplerBank> (std::move (*samplerBank)),
                        std::memory_order_release);
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

    // Per-track gain + equal-power pan for this block (continuous faders; voices
    // already ringing follow the live values). resize() only allocates when the
    // track count changes, so it's a no-op on the steady-state audio path.
    const size_t nTracks = proj != nullptr ? proj->tracks.size() : 0;
    if (trackMix.size() != nTracks)
        trackMix.resize (nTracks);
    for (size_t i = 0; i < nTracks; ++i)
    {
        const float pan   = juce::jlimit (-1.0f, 1.0f, proj->tracks[i].pan);
        const float theta = (pan * 0.5f + 0.5f) * juce::MathConstants<float>::halfPi;
        trackMix[i].gain = juce::jlimit (0.0f, 1.0f, proj->tracks[i].volume);
        trackMix[i].panL = std::cos (theta);
        trackMix[i].panR = std::sin (theta);
    }

    // Tails keep ringing even when stopped, so always render + master.
    mixer.renderInto (buffer, trackMix);

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

    // One atomic load of the sampler bank for the whole block (RCU read).
    const SamplerBankPtr bank = samplerSnapshot();
    if (bank == nullptr)
        return;

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
                if (ev.trackIndex < 0 || ev.trackIndex >= (int) bank->size())
                    return;
                const auto& smp = (*bank)[(size_t) ev.trackIndex];
                if (smp == nullptr)
                    return;

                const float s = ev.pStart.has_value() ? *ev.pStart : -1.0f;
                const float e = ev.pEnd.has_value()   ? *ev.pEnd   : -1.0f;
                const auto slice = smp->get (ev.velocity, s, e);
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
                v.pos         = (double) slice.start;
                v.endPos      = slice.start + slice.length;
                v.rate        = std::pow (2.0, (double) ev.pitchOffset / 12.0);   // varispeed pitch
                v.startOffset = startOffset;
                // Note-length gate (output samples); length <= 0 => one-shot.
                v.gateSamples = (ev.length > 0.0f)
                                  ? juce::jmax (1, (int) std::lround ((double) ev.length * samplesPer16))
                                  : 0;
                v.volume      = juce::jlimit (0.0f, 1.0f, (float) ev.velocity / 127.0f);
                v.trackIndex  = ev.trackIndex;     // per-track gain/pan applied in the mixer

                // Bake the per-voice TPT-SVF lowpass coeffs from the resolved
                // (p-locked) cutoff/resonance. Open cutoff => bypass (zero cost).
                if (ev.cutoff < 0.999f)
                {
                    const double fc = juce::jlimit (20.0, sampleRate * 0.49,
                                                    20.0 * std::pow (1000.0, (double) ev.cutoff));
                    const double Q  = 0.5 + (double) ev.resonance * 19.5;   // matches fx.py
                    const double k  = 1.0 / Q;
                    const double g  = std::tan (juce::MathConstants<double>::pi * fc / sampleRate);
                    const double a1 = 1.0 / (1.0 + g * (g + k));
                    v.filterOn = true;
                    v.svfA1    = (float) a1;
                    v.svfA2    = (float) (g * a1);
                    v.svfA3    = (float) (g * g * a1);   // a3 = g*a2
                }

                v.keepAlive   = smp;   // pin this sampler alive until the voice ends
                                       // (an RCU bank swap must not free a buffer a
                                       // ringing voice still points into)
                mixer.addVoice (v);
            });
        }
        ++idx;
    }
}

void SilaAudioProcessor::getStateInformation (juce::MemoryBlock& dest)
{
    // Message thread. Grab the immutable snapshot with one lock-free acquire-load
    // (same read the audio thread does — just bumps the shared_ptr refcount, never
    // blocks it), then serialise it as a property alongside the APVTS params.
    auto state = apvts.copyState();
    if (auto proj = snapshot())
    {
        // Only persist the project if it has loadable audio (≥1 assigned sample).
        // The in-code demo kit is synth buffers with no source paths, so it would
        // restore as silence — skip it so a fresh launch keeps the audible demo.
        bool hasSamples = false;
        for (const auto& t : proj->tracks)
            if (! t.samples.empty()) { hasSamples = true; break; }

        if (hasSamples)
            state.setProperty ("projectJson",
                               juce::JSON::toString (sila::engine::projectToVar (*proj), true), nullptr);
    }

    if (auto xml = state.createXml())
        copyXmlToBinary (*xml, dest);
}

void SilaAudioProcessor::setStateInformation (const void* data, int sizeInBytes)
{
    auto xml = getXmlFromBinary (data, sizeInBytes);
    if (xml == nullptr)
        return;

    const juce::ValueTree state = juce::ValueTree::fromXml (*xml);
    if (! state.isValid())
        return;

    apvts.replaceState (state);   // restore params (swing/songMode/master/...)

    // Restore the structural Project, if this preset carries one (older presets
    // without it just keep the current project).
    const juce::var projVar = juce::JSON::parse (state.getProperty ("projectJson").toString());
    if (! projVar.isObject())
        return;

    auto proj = std::make_shared<const sila::engine::Project> (sila::engine::projectFromVar (projVar));
    // Build the sampler bank now (WindowedSinc resamples each source file to the
    // current device rate). If this runs before prepareToPlay, sampleRate is the
    // default; prepareToPlay's rebuildSamplerBankForRate then re-resamples from
    // the same SampleRef paths to the real rate — self-healing.
    auto bank = std::make_shared<const SamplerBank> (buildBankForProject (*proj, sampleRate));
    setProject (std::move (proj), std::move (bank));
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
