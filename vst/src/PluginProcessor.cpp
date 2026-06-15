#include "PluginProcessor.h"
#include "PluginEditor.h"
#include "engine/ProjectJson.h"
#include <cmath>
#include <algorithm>

SilaAudioProcessor::SilaAudioProcessor()
    : juce::AudioProcessor (BusesProperties()
          .withOutput ("Output", juce::AudioChannelSet::stereo(), true)),
      apvts (*this, nullptr, "SILA", makeParameters())
{
    // Cache the per-slot raw-value pointers once (audio thread reads them lock-free).
    for (int s = 0; s < kMaxTracks; ++s)
    {
        const juce::String pfx = "t" + juce::String (s) + "_";
        pVol[s]    = apvts.getRawParameterValue (pfx + "vol");
        pPan[s]    = apvts.getRawParameterValue (pfx + "pan");
        pCutoff[s] = apvts.getRawParameterValue (pfx + "cutoff");
        pRes[s]    = apvts.getRawParameterValue (pfx + "res");
        pFmode[s]  = apvts.getRawParameterValue (pfx + "fmode");
    }

    // Same caching for the globals — read every block, so look the keys up once.
    pSwing        = apvts.getRawParameterValue ("swing");
    pSongMode     = apvts.getRawParameterValue ("songMode");
    pMasterVol    = apvts.getRawParameterValue ("masterVol");
    pSmallSpeaker = apvts.getRawParameterValue ("smallSpeaker");
}

SilaAudioProcessor::~SilaAudioProcessor() = default;

juce::AudioProcessorValueTreeState::ParameterLayout SilaAudioProcessor::makeParameters()
{
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

    // Per-track automation bank (Phase 6): a fixed set of slots, each with the
    // mixer/filter params. Tracks map to slots by index.
    for (int s = 0; s < kMaxTracks; ++s)
    {
        const String pfx = "t" + String (s) + "_";
        const String tn  = "Track " + String (s + 1) + " ";
        layout.add (std::make_unique<AudioParameterFloat>(
            ParameterID { pfx + "vol", 1 }, tn + "Volume", 0.0f, 1.0f, 1.0f));
        layout.add (std::make_unique<AudioParameterFloat>(
            ParameterID { pfx + "pan", 1 }, tn + "Pan", -1.0f, 1.0f, 0.0f));
        layout.add (std::make_unique<AudioParameterFloat>(
            ParameterID { pfx + "cutoff", 1 }, tn + "Cutoff", 0.0f, 1.0f, 1.0f));
        layout.add (std::make_unique<AudioParameterFloat>(
            ParameterID { pfx + "res", 1 }, tn + "Resonance", 0.0f, 1.0f, 0.0f));
        layout.add (std::make_unique<AudioParameterChoice>(
            ParameterID { pfx + "fmode", 1 }, tn + "Filter Mode",
            StringArray { "Low-pass", "High-pass", "Band-pass" }, 0));
    }
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

void SilaAudioProcessor::prepareToPlay (double sr, int /*samplesPerBlock*/)
{
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

juce::File SilaAudioProcessor::projectsDir()
{
    return juce::File::getSpecialLocation (juce::File::userHomeDirectory)
               .getChildFile ("SILA").getChildFile ("projects");
}

void SilaAudioProcessor::loadProject (ProjectPtr proj)
{
    if (proj == nullptr)
        return;
    auto bank = std::make_shared<const SamplerBank> (buildBankForProject (*proj, sampleRate));
    setProject (std::move (proj), std::move (bank));
}

void SilaAudioProcessor::addTrack (const juce::String& name)
{
    auto cur     = liveProject.load (std::memory_order_acquire);
    auto curBank = liveSamplers.load (std::memory_order_acquire);
    if (cur == nullptr || (int) cur->tracks.size() >= kMaxTracks)
        return;

    auto next = std::make_shared<sila::engine::Project> (*cur);
    sila::engine::Track t;
    t.id   = juce::Uuid().toString();   // stable id, separate from the name
    t.name = name;
    next->tracks.push_back (std::move (t));

    // Keep every MATERIALIZED pattern slot rectangular by appending a blank column
    // for the new track. Unauthored (empty) slots stay empty.
    for (auto& cols : next->patternBank.slots)
        if (! cols.empty())
            cols.push_back (std::vector<sila::engine::Step> (cols.front().size()));

    auto bank = std::make_shared<SamplerBank> (curBank != nullptr ? *curBank : SamplerBank {});
    auto smp  = std::make_shared<sila::engine::Sampler>();
    smp->prepare (sampleRate);          // empty (silent) until a sample is assigned
    bank->push_back (std::move (smp));

    setProject (std::move (next), std::make_shared<const SamplerBank> (std::move (*bank)));
}

void SilaAudioProcessor::removeTrack (int index)
{
    auto cur     = liveProject.load (std::memory_order_acquire);
    auto curBank = liveSamplers.load (std::memory_order_acquire);
    if (cur == nullptr || index < 0 || index >= (int) cur->tracks.size())
        return;

    auto next = std::make_shared<sila::engine::Project> (*cur);
    next->tracks.erase (next->tracks.begin() + index);
    // Pattern-bank columns are parallel to tracks by index — drop this one too.
    for (auto& slot : next->patternBank.slots)
        if (index < (int) slot.size())
            slot.erase (slot.begin() + index);

    auto bank = std::make_shared<SamplerBank> (curBank != nullptr ? *curBank : SamplerBank {});
    if (index < (int) bank->size())
        bank->erase (bank->begin() + index);

    setProject (std::move (next), std::make_shared<const SamplerBank> (std::move (*bank)));
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

    auto addTrack = [&] (const juce::String& name, juce::AudioBuffer<float> sample)
    {
        Track t;
        t.id   = name;
        t.name = name;
        proj->tracks.push_back (std::move (t));

        auto smp = std::make_shared<Sampler>();
        smp->prepare (sr);
        smp->addBuffer (std::move (sample));   // synthesized demo kit (no file path)
        samplerBank->push_back (std::move (smp));
    };
    addTrack ("Kick",  makeKick (sr));     // track 0
    addTrack ("Snare", makeSnare (sr));    // track 1
    addTrack ("Hat",   makeHat (sr));      // track 2

    // A 16-step column (one track's row in a pattern slot) with `on` steps active.
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

    // Unified pattern bank (Phase 6): step data lives here, parallel to tracks
    // (Kick=0, Snare=1, Hat=2). The grid + pattern mode play currentPattern (=0).
    auto& bank = proj->patternBank;

    // Slot 0 (A01) — the base groove: kick 4-on-the-floor, snare backbeat + a 1:2
    // ghost on step 14, offbeat hats with a 50% step and a micro-timed late hat.
    {
        auto kick  = steps16 ({ 0, 4, 8, 12 }, 110);
        auto snare = steps16 ({ 4, 12 }, 100);
        snare[14].active = true; snare[14].velocity = 70; snare[14].trig = TrigCondition::OneIn2;
        auto hats  = steps16 ({ 2, 6, 10, 14 }, 80);
        hats[6].microTiming  = 12;   // pushed late (clock.py: micro * interval / 6)
        hats[10].probability = 50;   // fires ~half the time
        bank.slots[0] = { kick, snare, hats };
    }
    // Slot 1 (A02) — variation: a kick pickup and straight-8th hats.
    bank.slots[1] = {
        steps16 ({ 0, 4, 8, 12, 14 }, 110),                 // kick + pickup on the "a" of 4
        steps16 ({ 4, 12 }, 100),                            // backbeat snare
        steps16 ({ 0, 2, 4, 6, 8, 10, 12, 14 }, 75),         // driving 8th-note hats
    };
    // Slot 2 (A03) — fill: a snare roll into the turnaround.
    bank.slots[2] = {
        steps16 ({ 0, 8 }, 110),                             // sparse kick
        steps16 ({ 8, 10, 12, 13, 14, 15 }, 95),             // snare roll
        steps16 ({ 2, 6, 10, 14 }, 80),                      // offbeat hats
    };

    proj->currentPattern = 0;            // grid + pattern mode show/play the base groove
    proj->songChain = { 0, 1, 0, 2 };    // legacy Phase 3b chain (unused by the engine)

    // Song Mode (Phase 6) demo arrangement so row-by-row playback is audible
    // without a UI. Rows reference the slots authored above; ↺ = repeat, +I = row
    // length in steps, MUTE = per-slot bitmask (kick=0, snare=1, hat=2).
    {
        using namespace sila::engine;
        Song song;
        song.name = "Demo Song";
        song.end  = SongEnd::Loop;
        //                  LABEL     PTN ↺  +I  BPM   MUTE
        song.rows.push_back ({ "INTRO",  0, 2, 16, 0.0f, (uint8_t) (1u << 1) }); // base groove, snare muted
        song.rows.push_back ({ "VERSE",  1, 2, 16, 0.0f, 0 });                   // variation
        song.rows.push_back ({ "FILL",   2, 1, 16, 0.0f, (uint8_t) (1u << 2) }); // snare roll, hat muted
        song.rows.push_back ({ "CHORUS", 1, 2, 16, 0.0f, 0 });                   // back to variation
        proj->songs.push_back (std::move (song));
        proj->activeSong = 0;
    }

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

    // Live performance scalars from the automatable params (cached atomic ptrs).
    const float swing      = pSwing    != nullptr ? pSwing->load()    : 0.0f;
    const bool  songMode   = pSongMode != nullptr && pSongMode->load() > 0.5f;
    const bool  fill       = fillActive.load (std::memory_order_relaxed);

    // Resolve the Song Mode position at the block start (a pure function of the
    // transport 16th — see Sequencer::resolveSong). Used for the row-tempo
    // override and the UI playhead; the per-boundary firing re-derives it too.
    sila::engine::SongPosition blockSong;
    if (proj != nullptr && playing && songMode)
    {
        const long absStart = (long) std::floor (ppqStart * 4.0 + 1e-9);
        blockSong = sila::engine::Sequencer::resolveSong (*proj, absStart);

        // Row BPM override is Standalone-only — a host owns the tempo/timeline, so
        // overriding there would fight the DAW grid. Sub-block row-boundary tempo
        // changes are clamped to the next block (same as sub-block swing).
        if (wrapperType == wrapperType_Standalone
            && blockSong.valid && ! blockSong.stopped && blockSong.tempo > 0.0f)
            bpm = (double) blockSong.tempo;
    }

    // Publish the transport position + status for the editor (C++ -> UI).
    currentPpq.store (ppqStart, std::memory_order_relaxed);
    transportPlaying.store (playing, std::memory_order_relaxed);
    currentBpm.store (bpm, std::memory_order_relaxed);
    currentSongSlot.store   (blockSong.valid ? blockSong.patternSlot : -1, std::memory_order_relaxed);
    currentSongRow.store    (blockSong.valid ? blockSong.row : -1, std::memory_order_relaxed);
    currentSongRepeat.store (blockSong.valid ? (int) blockSong.repeat : 0, std::memory_order_relaxed);

    if (proj != nullptr && playing && bpm > 0.0)
    {
        // Advance per-track free-run LFO phase for this block; free-mode voices
        // sample it at trigger (in scheduleTriggers).
        const size_t nt = proj->tracks.size();
        if (trackLfoPhase.size() != nt) trackLfoPhase.assign (nt, 0.0);
        const double twoPi = 2.0 * juce::MathConstants<double>::pi;
        for (size_t i = 0; i < nt; ++i)
        {
            trackLfoPhase[i] += numSamples * twoPi * (double) proj->tracks[i].lfoRate / sampleRate;
            while (trackLfoPhase[i] >= twoPi) trackLfoPhase[i] -= twoPi;
        }

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
        const int   slot = (int) i;
        const float vol  = (slot < kMaxTracks && pVol[slot] != nullptr) ? pVol[slot]->load() : 1.0f;
        const float pan  = (slot < kMaxTracks && pPan[slot] != nullptr) ? pPan[slot]->load() : 0.0f;
        const float theta = (juce::jlimit (-1.0f, 1.0f, pan) * 0.5f + 0.5f) * juce::MathConstants<float>::halfPi;
        trackMix[i].gain = juce::jlimit (0.0f, 1.0f, vol);
        trackMix[i].panL = std::cos (theta);
        trackMix[i].panR = std::sin (theta);
    }

    // Tails keep ringing even when stopped, so always render + master.
    mixer.renderInto (buffer, trackMix);

    jassert (pMasterVol != nullptr && pSmallSpeaker != nullptr);
    const float masterVol    = pMasterVol    != nullptr ? pMasterVol->load() : 1.0f;
    const bool  smallSpeaker = pSmallSpeaker != nullptr && pSmallSpeaker->load() > 0.5f;
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

    // Loop-varying boundary state, read by spawn() below. The voice-spawn body is
    // defined ONCE here so the song-mode and pattern-mode branches share it (no
    // duplication, identical DSP resolution in both).
    double offset = 0.0;
    long   absIdx = 0;

    auto spawn = [&] (const sila::engine::TrigEvent& ev)
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

                // Resolve the filter base from the APVTS slot bank; step p-locks
                // override. (The engine only passes the p-lock optionals through.)
                const int   fslot   = ev.trackIndex;
                const bool  inBank  = fslot >= 0 && fslot < kMaxTracks;
                const float baseCut = (inBank && pCutoff[fslot]) ? pCutoff[fslot]->load() : 1.0f;
                const float baseRes = (inBank && pRes[fslot])    ? pRes[fslot]->load()    : 0.0f;
                const auto  baseMode = (inBank && pFmode[fslot])
                                         ? (sila::engine::FilterMode) juce::roundToInt (pFmode[fslot]->load())
                                         : sila::engine::FilterMode::LowPass;
                const float cutoff = ev.pCutoff.value_or (baseCut);
                const float reso   = ev.pResonance.value_or (baseRes);
                const auto  fmode  = ev.pFilterMode.value_or (baseMode);

                // Base (pre-LFO) values the LFO modulates from at control rate.
                v.baseGain      = v.volume;
                v.baseRate      = v.rate;
                v.baseCutoff    = cutoff;
                v.baseResonance = reso;

                // Per-voice LFO: armed when depth & rate > 0. Trig-sync starts the
                // phase at 0; free-run samples the track's running phase so
                // overlapping voices stay aligned to the track LFO clock.
                const bool lfoOn = ev.lfoDepth > 0.0f && ev.lfoRate > 0.0f;
                if (lfoOn)
                {
                    v.lfo.on    = true;
                    v.lfo.shape = (int) ev.lfoShape;
                    v.lfo.dest  = (int) ev.lfoDest;
                    v.lfo.depth = ev.lfoDepth;
                    v.lfo.inc   = 2.0 * juce::MathConstants<double>::pi * (double) ev.lfoRate / sampleRate;
                    v.lfo.phase = ev.lfoSync ? 0.0
                                  : (ev.trackIndex >= 0 && ev.trackIndex < (int) trackLfoPhase.size()
                                         ? trackLfoPhase[(size_t) ev.trackIndex] : 0.0);
                    v.lfo.shVal = (ev.lfoShape == sila::engine::LfoShape::Random)
                                      ? lfoRng.nextFloat() * 2.0f - 1.0f : 0.0f;
                }

                // Filter engages when: LP with a non-open cutoff, any HP/BP mode,
                // or an LFO that sweeps cutoff. (LP at fully-open = transparent =
                // skip, for zero cost; the LFO update re-bakes coeffs each block.)
                const bool lfoCutoff = lfoOn && ev.lfoDest == sila::engine::LfoDest::Cutoff;
                const bool lpOpen    = fmode == sila::engine::FilterMode::LowPass && cutoff >= 0.999f;
                if (! lpOpen || lfoCutoff)
                {
                    v.filterOn = true;
                    v.svf.mode = fmode;
                    v.svf.bake (cutoff, reso, sampleRate);
                }

                v.keepAlive   = smp;   // pin this sampler alive until the voice ends
                                       // (an RCU bank swap must not free a buffer a
                                       // ringing voice still points into)
                mixer.addVoice (v);
            };

    long idx = (long) std::ceil (sixteenthStart - 1e-9);
    for (;;)
    {
        offset = (idx - sixteenthStart) * samplesPer16;
        if (offset >= numSamples)
            break;
        if (offset >= 0.0 && idx > lastFiredSixteenth)
        {
            lastFiredSixteenth = idx;
            absIdx = idx;

            // Song Mode derives the row / step / mutes from the absolute position
            // (Sequencer::resolveSong) and fires that pattern slot; if no song is
            // authored it falls back to plain pattern playback for this boundary.
            if (songMode)
            {
                const auto sp = sila::engine::Sequencer::resolveSong (proj, absIdx);
                if (sp.valid)
                    sequencer.forEachTrigSong (proj, sp, fill, spawn);
                else
                    sequencer.forEachTrig (proj, absIdx, fill, spawn);
            }
            else
            {
                sequencer.forEachTrig (proj, absIdx, fill, spawn);
            }
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
    return new SilaAudioProcessorEditor (*this);
}

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new SilaAudioProcessor();
}
