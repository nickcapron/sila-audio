#include "PluginProcessor.h"
#include "PluginEditor.h"
#include "engine/ProjectJson.h"
#include "SILAFactoryData.h"   // bundled factory sample pack (FactoryData namespace)
#include <cmath>
#include <algorithm>
#include <map>

juce::AudioProcessor::BusesProperties SilaAudioProcessor::makeBusesProperties()
{
    // Bus 0 = "Main" (the full summed mix, for plain stereo use). Buses 1..kMaxTracks
    // = one stereo aux per lane, so a host can route each track to its own channel /
    // FX chain (e.g. Reaper: bump the track channel count and add per-pair receives).
    auto props = BusesProperties().withOutput ("Main", juce::AudioChannelSet::stereo(), true);
    for (int i = 0; i < kMaxTracks; ++i)
        props = props.withOutput ("Track " + juce::String (i + 1),
                                  juce::AudioChannelSet::stereo(), true);
    return props;
}

bool SilaAudioProcessor::isBusesLayoutSupported (const BusesLayout& layouts) const
{
    // Main must be stereo; each per-lane aux bus is stereo or disabled (the host may
    // turn off lanes it isn't routing). No input buses (this is an instrument).
    if (layouts.getMainOutputChannelSet() != juce::AudioChannelSet::stereo())
        return false;

    for (int i = 1; i <= kMaxTracks; ++i)
    {
        const auto set = layouts.getChannelSet (false, i);
        if (! set.isDisabled() && set != juce::AudioChannelSet::stereo())
            return false;
    }
    return true;
}

SilaAudioProcessor::SilaAudioProcessor()
    : juce::AudioProcessor (makeBusesProperties()),
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

    // Install the bundled factory pack into ~/SILA/library on load (idempotent), so
    // the factory samples resolve for brand-new users. Independent of the audio
    // device (runs even before prepareToPlay).
    installFactoryLibrary();

    // Install the "Factory Showcase" EXAMPLE into ~/SILA/projects on first run
    // (once, marker-guarded). A fresh instance opens to a clean project; the
    // showcase is there to LOAD from the PROJECTS list, not to auto-play.
    installFactoryProject();
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

namespace
{
// Maps each bundled factory wav to its ~/SILA/library category folder (parallels
// the in-code kit paths in buildDemoProject). Filenames must match factory/*.wav.
struct FactoryMapEntry { const char* file; const char* category; };
const FactoryMapEntry kFactoryMap[] = {
    { "bass drum rd-6.wav",             "01. Kick" },
    { "rd-6 bass.wav",                  "01. Kick" },
    { "rd-6 bass distortion.wav",       "01. Kick" },
    { "snare drum rd-6.wav",            "02. Snare" },
    { "rd-6 snare.wav",                 "02. Snare" },
    { "rd-6 snare distortion.wav",      "02. Snare" },
    { "rd-6 clap.wav",                  "03. Clap" },
    { "rd-6 clap distortion.wav",       "03. Clap" },
    { "rd-6 closedhat.wav",             "04. Hi-Hat Closed" },
    { "rd-6 closed hat distortion.wav", "04. Hi-Hat Closed" },
    { "rd-6 openhat.wav",               "05. Hi-Hat Open" },
    { "rd-6 cymbal.wav",                "06. Cymbal" },
    { "rd-6 cymbal distortion.wav",     "06. Cymbal" },
    { "rd-6 hightom.wav",               "09. Tom" },
    { "rd-6 high tom distortion.wav",   "09. Tom" },
    { "rd-6 low tom.wav",               "09. Tom" },
    { "rd-6 low tom distortion.wav",    "09. Tom" },
    { "cz-1 mini bass 01.wav",          "21. Bass - Sub" },
    { "cz1 mini pluck bass.wav",        "27. Lead - Pluck" },
    { "cz1 mini crystal keys.wav",      "33. Keys - Piano" },
    { "cz1 choir vox pad.wav",          "32. Pad - Choir" },
};

// The 8-lane factory kit, shared by the clean default project and the showcase so
// their sample paths can't drift. `dist` swaps the four drum lanes to the RD-6
// distortion variants (the DROP's heavier kit). `movement` adds the synth motion
// (CZ bass acid wobble + choir-pad tremolo) — ON for the showcase, OFF for the
// clean starter so a new user's canvas is neutral.
std::vector<sila::engine::LaneSound> factoryKit (bool dist, bool movement)
{
    using namespace sila::engine;
    std::vector<LaneSound> k (8);
    k[0].samples = { SampleRef { dist ? "SILA Factory/01. Kick/rd-6 bass distortion.wav"
                                      : "SILA Factory/01. Kick/bass drum rd-6.wav" } };
    k[1].samples = { SampleRef { dist ? "SILA Factory/02. Snare/rd-6 snare distortion.wav"
                                      : "SILA Factory/02. Snare/rd-6 snare.wav" } };
    k[2].samples = { SampleRef { dist ? "SILA Factory/03. Clap/rd-6 clap distortion.wav"
                                      : "SILA Factory/03. Clap/rd-6 clap.wav" } };
    k[3].samples = { SampleRef { dist ? "SILA Factory/04. Hi-Hat Closed/rd-6 closed hat distortion.wav"
                                      : "SILA Factory/04. Hi-Hat Closed/rd-6 closedhat.wav" } };
    // The OH lane is velocity-LAYERED: soft hits = open hat, hard hits (vel >= 100)
    // = the RD-6 cymbal — a crash without spending a 9th lane.
    k[4].samples = { SampleRef { "SILA Factory/05. Hi-Hat Open/rd-6 openhat.wav", 0, 99 },
                     SampleRef { dist ? "SILA Factory/06. Cymbal/rd-6 cymbal distortion.wav"
                                      : "SILA Factory/06. Cymbal/rd-6 cymbal.wav", 100, 127 } };
    k[5].samples = { SampleRef { "SILA Factory/21. Bass - Sub/cz-1 mini bass 01.wav" } };
    k[6].samples = { SampleRef { "SILA Factory/33. Keys - Piano/cz1 mini crystal keys.wav" } };
    k[7].samples = { SampleRef { "SILA Factory/32. Pad - Choir/cz1 choir vox pad.wav" } };

    if (movement)
    {
        // A synced cutoff LFO gives the CZ bass an acid wobble; a slow free-run
        // tremolo breathes the pad. (The engine reads kit LFO in pattern + song mode.)
        k[5].lfoShape = LfoShape::Triangle; k[5].lfoDest = LfoDest::Cutoff;
        k[5].lfoRate = 6.0f; k[5].lfoDepth = 0.30f; k[5].lfoSync = true;
        k[5].cutoff = 0.55f; k[5].resonance = 0.55f;
        k[7].lfoShape = LfoShape::Sine; k[7].lfoDest = LfoDest::Volume;
        k[7].lfoRate = 0.5f; k[7].lfoDepth = 0.22f; k[7].lfoSync = false;
        k[7].cutoff = 0.7f;
    }
    return k;
}
}

void SilaAudioProcessor::installFactoryLibrary()
{
    const auto packDir = libraryRoot().getChildFile ("SILA Factory");

    for (int i = 0; i < FactoryData::namedResourceListSize; ++i)
    {
        const juce::String fn = FactoryData::originalFilenames[i];
        juce::String category;
        for (const auto& e : kFactoryMap)
            if (fn == e.file) { category = e.category; break; }
        if (category.isEmpty())
            continue;   // a bundled file we don't have a category for — skip

        const auto dest = packDir.getChildFile (category).getChildFile (fn);
        if (dest.existsAsFile())
            continue;   // already installed / user-present — never clobber

        int size = 0;
        const char* data = FactoryData::getNamedResource (FactoryData::namedResourceList[i], size);
        if (data == nullptr || size <= 0)
            continue;

        dest.getParentDirectory().createDirectory();
        dest.replaceWithData (data, (size_t) size);
    }
}

void SilaAudioProcessor::prepareToPlay (double sr, int /*samplesPerBlock*/)
{
    sampleRate = sr;
    internalPpq = 0.0;
    lastFiredSixteenth = -1;

    // Set the internal transport's default ONCE (independent of state-load order):
    // Standalone auto-plays (no host to start it); hosted starts stopped and waits
    // for the host transport (or the UI play button). Not persisted — wrapper-typed.
    if (! transportInitialized)
    {
        internalPlaying.store (wrapperType == wrapperType_Standalone, std::memory_order_relaxed);
        transportInitialized = true;
    }

    if (liveProject.load (std::memory_order_acquire) == nullptr)
    {
        // First prepare: author the clean default project + its sampler bank. The
        // factory pack it references is installed in the constructor (above), so the
        // sample files already resolve here. (State-load paths swap in saved work,
        // so this only fires for a genuinely fresh instance.)
        liveProject.store (buildDefaultProject (sr), std::memory_order_release);
    }
    else
    {
        // Re-prepare (device rate / block-size change): keep the project + user
        // edits, just re-resample the file-backed samplers to the new rate.
        rebuildSamplerBankForRate (sr);
    }

    mixer.prepare (sr);

    // Pre-reserve the per-track scratch vectors to the max so processBlock's
    // resize() on a track-count change never allocates on the audio thread.
    trackMix.reserve (kMaxTracks);
    trackLfoPhase.reserve (kMaxTracks);
}

void SilaAudioProcessor::reapRetired()
{
    // Message thread: free snapshots no audio-thread reader still holds (only
    // this retire list references them => use_count() == 1).
    retiredProjects.erase (
        std::remove_if (retiredProjects.begin(), retiredProjects.end(),
                        [] (const ProjectPtr& p) { return p.use_count() <= 1; }),
        retiredProjects.end());

    // Same reclamation for superseded sampler sets (audio thread done with them).
    retiredSamplers.erase (
        std::remove_if (retiredSamplers.begin(), retiredSamplers.end(),
                        [] (const SamplerSetPtr& b) { return b.use_count() <= 1; }),
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
    auto set = std::make_shared<const SamplerSet> (buildBankForProject (*proj, sampleRate));
    setProject (std::move (proj), std::move (set));
}

void SilaAudioProcessor::captureLaneParams (sila::engine::Project& proj, int slot)
{
    if (slot < 0 || slot >= sila::engine::PatternBank::kNumSlots)
        return;
    sila::engine::ensureKitLanes (proj, slot);
    auto& kit = proj.patternBank.kits[(size_t) slot];
    for (int lane = 0; lane < (int) kit.size() && lane < kMaxTracks; ++lane)
    {
        auto& ls = kit[(size_t) lane];
        ls.volume     = pVol[lane]    != nullptr ? pVol[lane]->load()    : 1.0f;
        ls.pan        = pPan[lane]    != nullptr ? pPan[lane]->load()    : 0.0f;
        ls.cutoff     = pCutoff[lane] != nullptr ? pCutoff[lane]->load() : 1.0f;
        ls.resonance  = pRes[lane]    != nullptr ? pRes[lane]->load()    : 0.0f;
        ls.filterMode = (sila::engine::FilterMode) juce::jlimit (0, 2,
                            juce::roundToInt (pFmode[lane] != nullptr ? pFmode[lane]->load() : 0.0f));
    }
}

void SilaAudioProcessor::recallLaneParams (int slot)
{
    auto snap = snapshot();
    if (snap == nullptr || slot < 0 || slot >= sila::engine::PatternBank::kNumSlots)
        return;
    const auto& kit = snap->patternBank.kits[(size_t) slot];
    auto setP = [this] (int lane, const char* pid, float value)
    {
        if (auto* p = apvts.getParameter ("t" + juce::String (lane) + "_" + pid))
            p->setValueNotifyingHost (p->convertTo0to1 (value));
    };
    // Recall every lane in the pool, not just the kit's: an unauthored slot has an
    // empty kit, so a missing lane recalls the DEFAULT mix (a fresh pattern starts
    // clean) rather than leaving the previous pattern's values lingering in APVTS.
    const int lanes = juce::jmin ((int) snap->tracks.size(), kMaxTracks);
    for (int lane = 0; lane < lanes; ++lane)
    {
        const sila::engine::LaneSound ls = lane < (int) kit.size() ? kit[(size_t) lane]
                                                                   : sila::engine::LaneSound {};
        setP (lane, "vol",    ls.volume);
        setP (lane, "pan",    ls.pan);
        setP (lane, "cutoff", ls.cutoff);
        setP (lane, "res",    ls.resonance);
        setP (lane, "fmode",  (float) (int) ls.filterMode);
    }
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
    // + a default (silent) kit lane for the new track. Unauthored slots stay empty.
    for (auto& cols : next->patternBank.slots)
        if (! cols.empty())
            cols.push_back (std::vector<sila::engine::Step> (cols.front().size()));
    for (auto& kit : next->patternBank.kits)
        if (! kit.empty())
            kit.push_back (sila::engine::LaneSound {});

    // Append a silent sampler lane to every authored (non-null) slot bank.
    auto set = std::make_shared<SamplerSet> (curBank != nullptr ? *curBank : SamplerSet {});
    for (auto& slotBank : *set)
        if (slotBank != nullptr)
        {
            auto nb  = std::make_shared<SamplerBank> (*slotBank);
            auto smp = std::make_shared<sila::engine::Sampler>();
            smp->prepare (sampleRate);      // empty (silent) until a sample is assigned
            nb->push_back (std::move (smp));
            slotBank = std::move (nb);
        }

    setProject (std::move (next), std::make_shared<const SamplerSet> (std::move (*set)));
}

void SilaAudioProcessor::removeTrack (int index)
{
    auto cur     = liveProject.load (std::memory_order_acquire);
    auto curBank = liveSamplers.load (std::memory_order_acquire);
    if (cur == nullptr || index < 0 || index >= (int) cur->tracks.size())
        return;

    auto next = std::make_shared<sila::engine::Project> (*cur);
    next->tracks.erase (next->tracks.begin() + index);
    // Pattern-bank columns + kit lanes are parallel to tracks by index — drop them.
    for (auto& slot : next->patternBank.slots)
        if (index < (int) slot.size())
            slot.erase (slot.begin() + index);
    for (auto& kit : next->patternBank.kits)
        if (index < (int) kit.size())
            kit.erase (kit.begin() + index);

    // Drop the lane from every authored (non-null) slot bank.
    auto set = std::make_shared<SamplerSet> (curBank != nullptr ? *curBank : SamplerSet {});
    for (auto& slotBank : *set)
        if (slotBank != nullptr && index < (int) slotBank->size())
        {
            auto nb = std::make_shared<SamplerBank> (*slotBank);
            nb->erase (nb->begin() + index);
            slotBank = std::move (nb);
        }

    setProject (std::move (next), std::make_shared<const SamplerSet> (std::move (*set)));
}

bool SilaAudioProcessor::auditionSample (const juce::File& file)
{
    if (! file.existsAsFile())
        return false;

    auto smp = std::make_shared<sila::engine::Sampler>();
    smp->prepare (sampleRate);
    if (! smp->addFile (file))      // decode + resample to the device rate (msg thread)
        return false;

    // Publish for the audio thread; a previous un-consumed audition is just dropped.
    pendingAudition.store (std::move (smp), std::memory_order_release);
    return true;
}

// Cache key for sharing one Sampler across pattern slots whose kit lane has an
// identical layer list (the common case: a kit copied per pattern) — dedups the
// decode + windowed-sinc resample and the buffer memory. The LANE INDEX is part
// of the key on purpose: Sampler::get() advances round-robin state, so sharing
// across lanes would interleave two tracks' RR streams. Sharing across slots is
// safe — only the active slot's sampler is triggered, and RR continuity across
// identical kits on a pattern switch is the musically expected behaviour.
static juce::String laneLayersKey (size_t lane, const std::vector<sila::engine::SampleRef>& layers)
{
    juce::String k ((juce::int64) lane);
    for (const auto& l : layers)
        k << '|' << l.path
          << '\x01' << l.velMin << '\x01' << l.velMax
          << '\x01' << juce::String (l.start, 9) << '\x01' << juce::String (l.end, 9)
          << '\x01' << l.rrGroup;
    return k;
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

void SilaAudioProcessor::assignTrackSamples (int slot, int lane,
                                             const std::vector<sila::engine::SampleRef>& layers)
{
    auto cur = liveSamplers.load (std::memory_order_acquire);
    if (cur == nullptr || slot < 0 || slot >= sila::engine::PatternBank::kNumSlots || lane < 0)
        return;

    // Copy the set (16 shared_ptrs, cheap), then rebuild just this (slot, lane)'s
    // sampler — other lanes keep their sampler + RR state, other slots untouched.
    auto next     = std::make_shared<SamplerSet> (*cur);
    auto slotBank = (*next)[(size_t) slot] != nullptr
                        ? std::make_shared<SamplerBank> (*(*next)[(size_t) slot])
                        : std::make_shared<SamplerBank>();
    if (lane >= (int) slotBank->size())
        slotBank->resize ((size_t) lane + 1);
    (*slotBank)[(size_t) lane] = buildSamplerFromLayers (layers, sampleRate);
    (*next)[(size_t) slot] = std::move (slotBank);

    auto old = liveSamplers.exchange (std::make_shared<const SamplerSet> (std::move (*next)),
                                      std::memory_order_acq_rel);
    if (old) retiredSamplers.push_back (std::move (old));
}

void SilaAudioProcessor::rebuildSamplerBankForRate (double sr)
{
    auto proj = liveProject.load (std::memory_order_acquire);
    auto cur  = liveSamplers.load (std::memory_order_acquire);
    if (proj == nullptr)
        return;

    auto set = std::make_shared<SamplerSet>();
    std::map<juce::String, std::shared_ptr<sila::engine::Sampler>> cache;   // see laneLayersKey
    for (int s = 0; s < sila::engine::PatternBank::kNumSlots; ++s)
    {
        const auto& kit = proj->patternBank.kits[(size_t) s];
        if (kit.empty())
            continue;   // unauthored slot => null bank
        auto bank = std::make_shared<SamplerBank>();
        bank->resize (kit.size());
        for (size_t i = 0; i < kit.size(); ++i)
        {
            if (! kit[i].samples.empty())
            {
                auto& shared = cache[laneLayersKey (i, kit[i].samples)];
                if (shared == nullptr)
                    shared = buildSamplerFromLayers (kit[i].samples, sr);   // re-resample at the new rate
                (*bank)[i] = shared;
            }
            else if (cur != nullptr && (*cur)[(size_t) s] != nullptr && i < (*cur)[(size_t) s]->size())
                (*bank)[i] = (*(*cur)[(size_t) s])[i];   // transitional synth kit: keep (built at the old rate)
        }
        (*set)[(size_t) s] = std::move (bank);
    }

    // prepareToPlay is not concurrent with processBlock, so the superseded set
    // has no audio-thread reader — store lets it free here, no retire list needed.
    liveSamplers.store (std::make_shared<const SamplerSet> (std::move (*set)),
                        std::memory_order_release);
}

SilaAudioProcessor::SamplerSet
SilaAudioProcessor::buildBankForProject (const sila::engine::Project& proj, double sr)
{
    SamplerSet set;   // all-null by default => unauthored slots are silent
    std::map<juce::String, std::shared_ptr<sila::engine::Sampler>> cache;   // see laneLayersKey
    for (int s = 0; s < sila::engine::PatternBank::kNumSlots; ++s)
    {
        const auto& kit = proj.patternBank.kits[(size_t) s];
        if (kit.empty())
            continue;
        auto bank = std::make_shared<SamplerBank>();
        bank->reserve (kit.size());
        for (size_t i = 0; i < kit.size(); ++i)
        {
            auto& shared = cache[laneLayersKey (i, kit[i].samples)];
            if (shared == nullptr)
                shared = buildSamplerFromLayers (kit[i].samples, sr);   // empty layers => silent sampler
            bank->push_back (shared);
        }
        set[(size_t) s] = std::move (bank);
    }
    return set;
}

void SilaAudioProcessor::setProject (ProjectPtr proj, SamplerSetPtr bank)
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

// The "Factory Showcase" EXAMPLE project (structure only — no sample rate, no
// sampler side effect; installFactoryProject serialises this to a real project
// file on first run). A 24-bar song in C minor from the bundled RD-6 drum kit +
// CZ-1 mini synth voices. It deliberately tours the feature set: per-pattern kits
// (distortion drums in the DROP, toms in the BUILD, a CZ-pluck melody voice in
// the BREAK), velocity LAYERS (the OH lane's hard hits are the RD-6 cymbal =
// crash), long patterns (32-step INTRO/DROP/OUTRO, a 64-step BREAK walking
// Cm - Ab - Eb - Bb), p-locks (the OUTRO bass cutoff walks down), plus trig
// conditions / probability / micro-timing / retrig. currentPattern 0 is the main
// groove; the SONG (Song Mode) is the full arrangement.
SilaAudioProcessor::ProjectPtr SilaAudioProcessor::makeShowcaseProject()
{
    using namespace sila::engine;

    auto proj = std::make_shared<Project>();

    // ── 8 factory tracks (RD-6 drums + CZ-1 mini synth) ──────────────────────
    const char* names[8]  = { "Kick", "Snare", "Perc", "CH", "OH", "Bass", "Keys", "Pad" };
    const char* colors[8] = { "#ff5a5a", "#ffae57", "#ffd23f", "#34e3c4",
                              "#5fd0e0", "#8b6cf0", "#c66cf0", "#6c8cf0" };
    for (int i = 0; i < 8; ++i)
    {
        Track t; t.id = names[i]; t.name = names[i]; t.color = colors[i];
        proj->tracks.push_back (std::move (t));
    }
    proj->keyRoot = 0; proj->keyScale = "minor";   // C minor — drives the keyboard UI

    // Per-pattern kit: `dist` swaps the drum lanes to the RD-6 distortion variants
    // (see factoryKit). Movement (synth LFOs) on for the showcase.
    auto makeKit = [] (bool dist) { return factoryKit (dist, true); };

    // Column helpers: Cx/Cnx build an n-step column (16 = 1 bar, 32 = 2 bars, …);
    // Cx = drum hits {step, vel}, Cnx = pitched notes {step, vel, semitones from C}.
    // C/Cn are the 1-bar shorthands; sustain() gates a column's active steps.
    auto Cx = [] (int n, std::initializer_list<std::pair<int,int>> hits)
    {
        std::vector<Step> v ((size_t) n);
        for (auto h : hits) { auto& s = v[(size_t) h.first]; s.active = true; s.velocity = h.second; }
        return v;
    };
    auto Cnx = [] (int n, std::initializer_list<std::array<int,3>> hits)
    {
        std::vector<Step> v ((size_t) n);
        for (auto h : hits) { auto& s = v[(size_t) h[0]]; s.active = true; s.velocity = h[1]; s.pitchOffset = h[2]; }
        return v;
    };
    auto C  = [&] (std::initializer_list<std::pair<int,int>> hits) { return Cx (16, hits); };
    auto Cn = [&] (std::initializer_list<std::array<int,3>> hits) { return Cnx (16, hits); };
    auto sustain = [] (std::vector<Step> v, float len)
    {
        for (auto& s : v) if (s.active) s.length = len;
        return v;
    };
    auto empty = [] (int n = 16) { return std::vector<Step> ((size_t) n); };

    // The driving 16th acid bass, reused by the GROOVE/BUILD patterns.
    auto grooveBass = [&] { return Cn ({ {0,112,0},{2,78,0},{3,86,12},{4,96,0},{6,78,0},{7,86,10},
                                         {8,112,0},{10,78,0},{11,86,12},{12,96,7},{14,80,5},{15,86,0} }); };

    auto& bank = proj->patternBank;

    // ── Slot 0 · GROOVE (the main loop) — kept SPACIOUS on purpose: it's home
    //    base, so the BUILD/DROP add energy to it rather than competing. Clap sits
    //    out (the snare owns the backbeat), the bass breathes instead of running
    //    16ths (that driving line is saved for the BUILD), hats are a clean pulse. ─
    {
        auto snare = C ({ {4,108},{12,108},{7,60} });
        snare[7].trig  = TrigCondition::OneIn2;     // one ghost, every other bar
        auto ch = C ({ {0,52},{2,60},{4,52},{6,60},{8,52},{10,60},{12,52},{14,60} });
        ch[6].microTiming = 10;                     // a touch of shuffle on the "&"
        bank.slots[0] = {
            C ({ {0,115},{4,115},{8,115},{12,115} }),   // kick 4-on-the-floor
            snare,
            empty(),                                    // clap rests in the groove
            ch,                                         // steady offbeat hats
            C ({ {6,60},{14,68} }),                     // open-hat lift on the "&"s
            // Spacious bass: root + an octave pickup + a fifth — room between hits.
            Cn ({ {0,110,0},{3,82,12},{8,104,0},{11,82,12},{14,80,7} }),
            sustain (Cn ({ {2,84,7},{10,80,3} }), 2.0f),// two crystal-key stabs
            sustain (Cn ({ {0,52,0} }), 16.0f),         // sustained choir pad
        };
    }
    // ── Slot 1 · INTRO (2 bars: pad-led bar 1; hats + kick pickup arrive bar 2,
    //    a Bb bass pickup pulls the ear back to C for the groove) ──────────────
    bank.slots[1] = {
        Cx (32, { {0,100},{16,100},{24,88},{30,72} }),
        empty (32), empty (32),
        Cx (32, { {18,48},{22,48},{26,48},{30,50} }),
        empty (32),
        Cnx (32, { {0,90,0},{16,90,0},{28,72,-2} }),
        sustain (Cnx (32, { {8,58,12},{24,58,15} }), 3.0f),
        sustain (Cnx (32, { {0,55,0},{16,55,0} }), 16.0f),
    };
    // ── Slot 2 · BUILD (rising tension into the drop) ────────────────────────
    {
        auto snare = C ({ {8,90},{10,95},{12,100},{13,105},{14,110},{15,118} });
        snare[13].retrig = 2; snare[14].retrig = 3;
        snare[15].retrig = 4; snare[15].retrigFade = 0.5f;   // accelerating roll
        bank.slots[2] = {
            C ({ {0,112},{4,112},{8,112},{12,112} }), snare,
            C ({ {1,84},{3,108},{5,88},{7,112} }),   // hi/lo tom run (velocity layers)
            C ({ {0,52},{1,42},{2,54},{3,42},{4,52},{5,42},{6,54},{7,42},
                 {8,52},{9,42},{10,54},{11,42},{12,52},{13,42},{14,54},{15,46} }),
            C ({ {14,70} }),
            grooveBass(),
            sustain (Cn ({ {2,84,7},{6,84,10},{10,84,12},{14,84,15} }), 2.0f),   // climbing stabs
            sustain (Cn ({ {0,55,7} }), 16.0f),      // pad holds the dominant
        };
    }
    // ── Slot 3 · DROP (2 bars, RD-6 distortion kit, heaviest; the vel-120 hit on
    //    the OH lane's downbeat is the CRASH via its velocity layer) ────────────
    {
        auto clap = Cx (32, { {4,66},{12,66},{20,66},{28,66},{7,52} });
        clap[7].trig = TrigCondition::OneIn2;
        bank.slots[3] = {
            Cx (32, { {0,120},{4,120},{8,120},{12,120},{14,90},
                      {16,120},{20,120},{24,120},{28,120},{30,95} }),
            Cx (32, { {4,115},{12,115},{20,115},{28,115} }),
            clap,
            Cx (32, { {0,60},{2,60},{4,60},{6,60},{8,60},{10,60},{12,60},{14,60},
                      {16,60},{18,60},{20,60},{22,60},{24,60},{26,60},{28,60},{30,60} }),
            Cx (32, { {0,120},{2,70},{6,70},{10,70},{14,70},{18,70},{22,70},{26,70},{30,70} }),
            Cnx (32, { {0,120,0},{2,100,0},{4,120,12},{6,100,0},{8,120,0},{10,100,0},{12,120,12},{14,100,7},
                       {16,120,0},{18,100,0},{20,120,12},{22,100,0},{24,120,0},{26,100,0},{28,120,15},{30,110,12} }),
            sustain (Cnx (32, { {0,95,12},{4,95,15},{8,95,12},{12,95,19},
                                {16,95,12},{20,95,15},{24,95,12},{28,95,17} }), 2.0f),
            sustain (Cnx (32, { {0,60,0},{16,60,0} }), 16.0f),
        };
    }
    // ── Slot 4 · BREAK (4 bars — the harmonic centrepiece: Cm → Ab → Eb → Bb,
    //    voice-led pad roots, CZ-pluck melody (kit-swapped), kick returns bar 4) ─
    {
        const int r[4] = { 0, -4, 3, -2 };            // bar roots: Cm  Ab  Eb  Bb
        std::vector<Step> bass (64), pad (64), ch (64);
        for (int b = 0; b < 4; ++b)
        {
            auto on = [&] (std::vector<Step>& col, int step, int vel, int note, float len = 0.0f)
            {
                auto& s = col[(size_t) (b * 16 + step)];
                s.active = true; s.velocity = vel; s.pitchOffset = note; s.length = len;
            };
            on (bass, 0, 82, r[b]);  on (bass, 8, 74, r[b]);  on (bass, 12, 70, r[b] + 7);
            on (pad,  0, 60, r[b], 16.0f);
            for (int st : { 2, 6, 10, 14 }) on (ch, st, 44, 0);
        }
        auto melody = sustain (Cnx (64, {
            {0,90,12},{4,85,15},{8,88,10},{12,84,7},          // Cm — falling answer
            {16,88,8},{20,84,12},{24,86,15},{28,82,12},       // Ab — lifts
            {32,90,15},{36,85,19},{40,88,15},{44,84,10},      // Eb — the peak
            {48,88,10},{52,84,14},{56,86,17},{60,88,19} }),   // Bb — climbs back in
            2.0f);
        bank.slots[4] = {
            Cx (64, { {48,88},{56,88} }),                     // heartbeat returns in bar 4
            empty (64), empty (64),
            ch, empty (64),
            bass, melody, pad,
        };
    }
    // ── Slot 5 · OUTRO (2 bars, wind down: p-locked cutoff walks the bass darker
    //    with each note; a soft crash opens it) ────────────────────────────────
    {
        auto bass = Cnx (32, { {0,80,0},{8,75,0},{16,72,0},{24,66,0} });
        const float fade[4] = { 0.50f, 0.40f, 0.30f, 0.22f };
        int fi = 0;
        for (auto& s : bass) if (s.active) s.pCutoff = fade[fi++];
        bank.slots[5] = {
            Cx (32, { {0,100},{8,85},{16,92},{24,72} }),
            empty (32), empty (32),
            Cx (32, { {4,36},{12,34},{20,30},{28,26} }),
            Cx (32, { {0,104} }),                            // soft crash into the outro
            bass,
            sustain (Cnx (32, { {8,55,12},{24,45,7} }), 4.0f),   // last key echoes
            sustain (Cnx (32, { {0,55,0},{16,50,0} }), 16.0f),
        };
    }

    // Per-pattern kits: clean RD-6 everywhere except the DROP, which swaps in the
    // distortion drums (the kit-per-pattern showcase).
    for (int s : { 0, 1, 2, 4, 5 }) bank.kits[(size_t) s] = makeKit (false);
    bank.kits[3] = makeKit (true);
    // BUILD: the Perc lane becomes the RD-6 toms (hi soft / lo hard velocity
    // split) — kit-per-pattern means different SOUNDS, not just FX variants.
    bank.kits[2][2].samples = { SampleRef { "SILA Factory/09. Tom/rd-6 hightom.wav", 0, 99 },
                                SampleRef { "SILA Factory/09. Tom/rd-6 low tom.wav", 100, 127 } };
    // BREAK: the Keys lane swaps to the CZ pluck (a new voice marks the scene
    // change) and the choir pad breathes a little deeper.
    bank.kits[4][6].samples = { SampleRef { "SILA Factory/27. Lead - Pluck/cz1 mini pluck bass.wav" } };
    bank.kits[4][7].lfoDepth = 0.30f;

    proj->currentPattern = 0;            // pattern mode shows/plays the main groove

    // ── Extended song (Song Mode): a 22-bar arrangement of the patterns above ─
    {
        Song song;
        song.name = "Factory Showcase";
        song.end  = SongEnd::Loop;
        // Row +I must equal the pattern's authored length (the pattern WRAPS
        // inside the row): INTRO/DROP/OUTRO are 32-step, BREAK is 64-step.
        //                    LABEL      PTN ↺  +I  BPM   MUTE
        song.rows.push_back ({ "INTRO",   1, 1, 32, 0.0f, 0 });
        song.rows.push_back ({ "GROOVE",  0, 4, 16, 0.0f, 0 });
        song.rows.push_back ({ "BUILD",   2, 2, 16, 0.0f, 0 });
        song.rows.push_back ({ "DROP",    3, 2, 32, 0.0f, 0 });
        song.rows.push_back ({ "BREAK",   4, 1, 64, 0.0f, 0 });
        song.rows.push_back ({ "BUILD",   2, 2, 16, 0.0f, 0 });
        song.rows.push_back ({ "DROP",    3, 2, 32, 0.0f, 0 });
        song.rows.push_back ({ "OUTRO",   5, 1, 32, 0.0f, 0 });
        proj->songs.push_back (std::move (song));
        proj->activeSong = 0;
    }

    return proj;   // pure structure; the caller builds the sampler bank
}

// The clean starter a fresh instance opens to: the 8 factory tracks with the
// clean kit loaded (anything the user programs is immediately audible), but
// EMPTY patterns and no song — nothing plays by itself. This replaces the old
// auto-playing baked-in demo; the showcase now lives in the PROJECTS list.
SilaAudioProcessor::ProjectPtr SilaAudioProcessor::buildDefaultProject (double sr)
{
    using namespace sila::engine;

    auto proj = std::make_shared<Project>();

    const char* names[8]  = { "Kick", "Snare", "Perc", "CH", "OH", "Bass", "Keys", "Pad" };
    const char* colors[8] = { "#ff5a5a", "#ffae57", "#ffd23f", "#34e3c4",
                              "#5fd0e0", "#8b6cf0", "#c66cf0", "#6c8cf0" };
    for (int i = 0; i < 8; ++i)
    {
        Track t; t.id = names[i]; t.name = names[i]; t.color = colors[i];
        proj->tracks.push_back (std::move (t));
    }
    proj->keyRoot = 0; proj->keyScale = "minor";

    // Kit only, no steps: pattern 0 has the clean sounds loaded (no synth movement
    // — a neutral canvas) while its step columns stay unauthored (silent). The UI
    // renders an empty grid for an unauthored slot, so the user sees 8 empty lanes,
    // each already holding a sound.
    proj->patternBank.kits[0] = factoryKit (false, false);
    proj->currentPattern = 0;

    auto set = std::make_shared<const SamplerSet> (buildBankForProject (*proj, sr));
    liveSamplers.store (set, std::memory_order_release);
    return proj;
}

void SilaAudioProcessor::installFactoryProject()
{
    using namespace sila::engine;

    const auto silaRoot = juce::File::getSpecialLocation (juce::File::userHomeDirectory)
                              .getChildFile ("SILA");
    const auto marker = silaRoot.getChildFile (".factory_project_installed");
    if (marker.existsAsFile())
        return;   // ran once already — respect a user who deleted the showcase

    const auto file = projectsDir().getChildFile ("Factory Showcase.json");
    if (! file.existsAsFile())   // never clobber a same-named file the user has
    {
        auto proj = makeShowcaseProject();

        // Write the same { project, params } shape the Save route produces, so
        // loading the showcase behaves exactly like loading a user project. The
        // params block encodes pattern 0's kit mix; convertTo0to1 keeps it robust
        // to parameter-range changes.
        auto* root   = new juce::DynamicObject();
        root->setProperty ("project", projectToVar (*proj));

        auto* params = new juce::DynamicObject();
        auto setNorm = [&] (const juce::String& id, float value)
        {
            if (auto* p = apvts.getParameter (id))
                params->setProperty (id, (double) p->convertTo0to1 (value));
        };
        const auto& kit0 = proj->patternBank.kits[0];
        for (int s = 0; s < kMaxTracks; ++s)
        {
            const LaneSound ls = s < (int) kit0.size() ? kit0[(size_t) s] : LaneSound {};
            const juce::String pfx = "t" + juce::String (s) + "_";
            setNorm (pfx + "vol",    ls.volume);
            setNorm (pfx + "pan",    ls.pan);
            setNorm (pfx + "cutoff", ls.cutoff);
            setNorm (pfx + "res",    ls.resonance);
            setNorm (pfx + "fmode",  (float) (int) ls.filterMode);
        }
        setNorm ("masterVol", 1.0f);
        setNorm ("swing", 0.0f);
        setNorm ("smallSpeaker", 0.0f);
        setNorm ("songMode", 1.0f);   // load the showcase with its arrangement engaged
        root->setProperty ("params", juce::var (params));

        projectsDir().createDirectory();
        file.replaceWithText (juce::JSON::toString (juce::var (root), false));
    }

    silaRoot.createDirectory();
    marker.create();   // only ever attempt the install once
}

void SilaAudioProcessor::processBlock (juce::AudioBuffer<float>& buffer,
                                       juce::MidiBuffer& midi)
{
    juce::ScopedNoDenormals noDenormals;
    buffer.clear();

    const int numSamples = buffer.getNumSamples();

    // --- Resolve transport: host if playing, else the UI internal transport ----
    double bpm = kDefaultBpm, ppqStart = internalPpq;
    bool playing = false, hostPlaying = false;
    bool haveHostBpm = false; double hostBpm = kDefaultBpm;

    if (auto* ph = getPlayHead())
    {
        if (auto pos = ph->getPosition())
        {
            // The host reports its project tempo even while the transport is
            // parked, so capture it unconditionally — that lets SILA track a
            // tempo change made in the DAW while stopped. Standalone has no real
            // host (the wrapper's playhead is meaningless), so skip it there and
            // let the UI wheel govern as before.
            if (wrapperType != wrapperType_Standalone)
                if (auto hb = pos->getBpm()) { hostBpm = *hb; haveHostBpm = true; }

            if (pos->getIsPlaying())
            {
                hostPlaying = true;
                playing     = true;
                bpm         = haveHostBpm ? hostBpm : kDefaultBpm;
                ppqStart    = pos->getPpqPosition().orFallback (internalPpq);
            }
        }
    }

    if (! hostPlaying)
    {
        // No host transport running. Stop resets the playhead to the top
        // (groovebox-style) — done here on the audio thread so internalPpq is
        // never written from the message thread (no data race).
        const bool intPlay = internalPlaying.load (std::memory_order_relaxed);
        if (wasInternalPlaying && ! intPlay) { internalPpq = 0.0; lastFiredSixteenth = -1; }
        wasInternalPlaying = intPlay;
        playing  = intPlay;
        // A present-but-parked DAW still owns tempo (so the readout/clock match
        // the project); only true Standalone falls back to the UI wheel.
        bpm      = haveHostBpm ? hostBpm : internalBpm.load (std::memory_order_relaxed);
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
        // resize (not assign) so existing tracks keep their running phase — only a
        // newly added track starts at 0. Capacity is reserved to kMaxTracks in
        // prepareToPlay, so this never allocates on the audio thread.
        if (trackLfoPhase.size() != nt) trackLfoPhase.resize (nt, 0.0);
        const double twoPi = 2.0 * juce::MathConstants<double>::pi;
        // The free-run LFO rate is per-pattern now (Phase 7): read it from the
        // active slot's kit (the song's row slot in song mode, else currentPattern).
        const int lfoSlot = juce::jlimit (0, sila::engine::PatternBank::kNumSlots - 1,
                                          (songMode && blockSong.valid) ? blockSong.patternSlot
                                                                        : proj->currentPattern);
        const auto& lfoKit = proj->patternBank.kits[(size_t) lfoSlot];
        for (size_t i = 0; i < nt; ++i)
        {
            const double rate = i < lfoKit.size() ? (double) lfoKit[i].lfoRate : 1.0;
            trackLfoPhase[i] += numSamples * twoPi * rate / sampleRate;
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

    // Live MIDI note input — mirrors the MIDI *export* map, so SILA's own exported
    // song, played back into SILA, reproduces the performance: channel N triggers
    // lane N-1; note 60 (C3) = the lane's programmed pitch, other notes transpose
    // via varispeed; velocity drives level + layer selection. Pad semantics:
    // one-shot (note-offs ignored), works with the transport stopped, and — like
    // the hardware — manual hits sound even on muted lanes (mute gates the
    // sequencer, not the pads). Sample-accurate via the event's block position.
    if (proj != nullptr && ! midi.isEmpty())
    {
        if (const SamplerSetPtr bankSet = samplerSnapshot())
        {
            // Notes play the pattern you're hearing: the song row's slot when song
            // mode is rolling, else the edited pattern.
            const int slot = juce::jlimit (0, sila::engine::PatternBank::kNumSlots - 1,
                                           (songMode && blockSong.valid && ! blockSong.stopped)
                                               ? blockSong.patternSlot : proj->currentPattern);
            const auto& bank = (*bankSet)[(size_t) slot];
            const double samplesPer16 = sampleRate * 60.0 / (bpm > 0.0 ? bpm : kDefaultBpm) / 4.0;

            for (const auto meta : midi)
            {
                const auto msg = meta.getMessage();
                if (! msg.isNoteOn())
                    continue;
                const int lane = msg.getChannel() - 1;         // export convention: lane 0 = ch 1
                if (lane < 0 || lane >= (int) proj->tracks.size())
                    continue;
                const auto* snd = sila::engine::Sequencer::laneSound (*proj, slot, lane);
                if (snd != nullptr && ! snd->active)
                    continue;                                  // lane hidden in this pattern

                sila::engine::TrigEvent ev;
                ev.trackIndex  = lane;
                ev.velocity    = (int) msg.getVelocity();
                ev.pitchOffset = juce::jlimit (-24, 24, msg.getNoteNumber() - 60);
                ev.length      = 0.0f;                         // one-shot (no gate)
                // Same per-pattern LFO resolution as a sequenced trig (no p-locks).
                if (snd != nullptr)
                {
                    ev.lfoShape = snd->lfoShape;
                    ev.lfoDest  = snd->lfoDest;
                    ev.lfoRate  = snd->lfoRate;
                    ev.lfoDepth = snd->lfoDepth;
                    ev.lfoSync  = snd->lfoSync;
                }
                spawnVoice (ev, meta.samplePosition, bank.get(), samplesPer16);
            }
        }
    }

    // Library audition: consume a pending preview (one per block) and spawn a
    // one-shot voice through the master bus. Works whether or not the transport is
    // playing; trackIndex = -1 => unity gain/pan (no per-track mix). The sampler is
    // pinned by keepAlive for the voice's lifetime (RT-safe, no dealloc race).
    if (auto preview = pendingAudition.exchange (nullptr, std::memory_order_acquire))
    {
        const auto slice = preview->get (127);
        if (slice.buffer != nullptr)
        {
            sila::engine::Voice v;
            v.audio      = slice.buffer;
            v.pos        = (double) slice.start;
            v.endPos     = slice.start + slice.length;
            v.trackIndex = -1;                 // unity mix (not a project track)
            v.keepAlive  = std::move (preview);
            mixer.addVoice (v);
        }
    }

    // Multi-out: the Main bus (0) carries the full summed mix; each enabled aux bus
    // (1..kMaxTracks) carries one lane's stem (pre-master) for per-track Reaper FX.
    // renderInto writes the same per-track signal to both, so the stems sum to the
    // Main mix by construction. Master (vol + small-speaker) applies to Main only —
    // the per-lane stems stay clean for downstream processing.
    auto mainBus = getBusBuffer (buffer, false, 0);

    sila::engine::LaneOut lanes[kMaxTracks] = {};
    for (int i = 0; i < kMaxTracks; ++i)
    {
        const int busIdx = i + 1;
        if (auto* b = getBus (false, busIdx); b != nullptr && b->isEnabled())
        {
            auto laneBus = getBusBuffer (buffer, false, busIdx);   // referencing view (no copy)
            lanes[i].L = laneBus.getWritePointer (0);
            lanes[i].R = laneBus.getNumChannels() > 1 ? laneBus.getWritePointer (1) : nullptr;
        }
    }

    // Tails keep ringing even when stopped, so always render + master.
    mixer.renderInto (mainBus, trackMix, lanes, kMaxTracks);

    jassert (pMasterVol != nullptr && pSmallSpeaker != nullptr);
    const float masterVol    = pMasterVol    != nullptr ? pMasterVol->load() : 1.0f;
    const bool  smallSpeaker = pSmallSpeaker != nullptr && pSmallSpeaker->load() > 0.5f;
    mixer.applyMaster (mainBus, smallSpeaker, masterVol);
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

    // One atomic load of the sampler SET for the whole block (RCU read). Phase 7:
    // the bank for the ACTIVE slot is selected per boundary (activeSlot below).
    const SamplerSetPtr bankSet = samplerSnapshot();
    if (bankSet == nullptr)
        return;

    // Loop-varying boundary state, read by spawn() below. The voice-spawn body is
    // defined ONCE here so the song-mode and pattern-mode branches share it (no
    // duplication, identical DSP resolution in both). activeSlot selects which
    // pattern's kit/bank plays this boundary (currentPattern, or the song's slot).
    double offset    = 0.0;
    long   absIdx    = 0;
    int    activeSlot = 0;

    auto spawn = [&] (const sila::engine::TrigEvent& ev)
            {
                if (activeSlot < 0 || activeSlot >= (int) bankSet->size())
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

                spawnVoice (ev, startOffset, (*bankSet)[(size_t) activeSlot].get(), samplesPer16);
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
                {
                    activeSlot = sp.patternSlot;     // song row's pattern -> its kit/bank
                    sequencer.forEachTrigSong (proj, sp, fill, spawn);
                }
                else
                {
                    activeSlot = proj.currentPattern;
                    sequencer.forEachTrig (proj, absIdx, fill, spawn);
                }
            }
            else
            {
                activeSlot = proj.currentPattern;    // pattern mode -> the edited pattern's kit
                sequencer.forEachTrig (proj, absIdx, fill, spawn);
            }
        }
        ++idx;
    }
}

// Build + enqueue the voice(s) for one trigger — the single spawn path shared by
// the sequencer (pattern + song mode) and live MIDI note input, so their DSP
// resolution (velocity layers, filter base, LFO, retrig) can never drift. Audio
// thread, allocation-free. `bank` = the ACTIVE pattern slot's sampler bank
// (null => unauthored/silent); `startOffset` = sample position within the block.
void SilaAudioProcessor::spawnVoice (const sila::engine::TrigEvent& ev, int startOffset,
                                     const SamplerBank* bank, double samplesPer16)
{
    if (bank == nullptr)
        return;                       // unauthored slot => silent
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

    // Resolve the filter base from the APVTS slot bank; step p-locks override.
    // (The engine only passes the p-lock optionals through.)
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

    // Per-voice LFO: armed when depth & rate > 0. Trig-sync starts the phase at 0;
    // free-run samples the track's running phase so overlapping voices stay
    // aligned to the track LFO clock.
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

    // Filter engages when: LP with a non-open cutoff, any HP/BP mode, or an LFO
    // that sweeps cutoff. (LP at fully-open = transparent = skip, for zero cost;
    // the LFO update re-bakes coeffs each block.)
    const bool lfoCutoff = lfoOn && ev.lfoDest == sila::engine::LfoDest::Cutoff;
    const bool lpOpen    = fmode == sila::engine::FilterMode::LowPass && cutoff >= 0.999f;
    if (! lpOpen || lfoCutoff)
    {
        v.filterOn = true;
        v.svf.mode = fmode;
        v.svf.bake (cutoff, reso, sampleRate);
    }

    v.keepAlive   = smp;   // pin this sampler alive until the voice ends (an RCU
                           // bank swap must not free a buffer a ringing voice
                           // still points into)

    const int rt = juce::jlimit (1, 8, ev.retrig);
    if (rt <= 1)
    {
        mixer.addVoice (v);
    }
    else
    {
        // Retrig (ratchet): re-fire the sample rt times evenly across the step,
        // each a fresh copy restarting at the slice start, with an optional
        // velocity ramp (+ swells up, - fades out). Copies inherit the fully-built
        // voice (filter/LFO/gate + the keepAlive pin).
        const double spacing = samplesPer16 / (double) rt;
        const float  fade    = juce::jlimit (-1.0f, 1.0f, ev.retrigFade);
        for (int k = 0; k < rt; ++k)
        {
            sila::engine::Voice rv = v;
            rv.startOffset = startOffset + (int) std::lround (k * spacing);
            const float t    = (float) k / (float) (rt - 1);
            const float mult = fade >= 0.0f ? (1.0f - fade * (1.0f - t))
                                            : (1.0f + fade * t);
            rv.volume   = juce::jlimit (0.0f, 1.0f, v.volume * mult);
            rv.baseGain = rv.volume;
            mixer.addVoice (rv);
        }
    }
}

void SilaAudioProcessor::getStateInformation (juce::MemoryBlock& dest)
{
    // Message thread. Grab the immutable snapshot with one lock-free acquire-load
    // (same read the audio thread does — just bumps the shared_ptr refcount, never
    // blocks it), then serialise it as a property alongside the APVTS params.
    //
    // ALWAYS persist the full project: programmed sequence data (patterns + songs)
    // must survive a host save even when no sample is assigned — losing an
    // arrangement over a missing .wav is unacceptable. Tradeoff: a project whose
    // only sound is the in-code demo synth kit (no source paths) reloads SILENT,
    // since those buffers can't be reconstructed from JSON. Reconstructing the demo
    // kit on load is deferred polish; real (sampled) projects round-trip fully.
    auto state = apvts.copyState();
    if (auto proj = snapshot())
    {
        // Phase 7c: flush the current pattern's live APVTS mix into its kit so the
        // saved project carries the latest knob tweaks (other patterns captured on
        // switch-away). Done on a LOCAL COPY — a save must not mutate/republish the
        // live project (getStateInformation may run off the message thread).
        sila::engine::Project toSave = *proj;
        captureLaneParams (toSave, juce::jlimit (0, sila::engine::PatternBank::kNumSlots - 1, toSave.currentPattern));
        state.setProperty ("projectJson",
                           juce::JSON::toString (sila::engine::projectToVar (toSave), true), nullptr);
    }
    state.setProperty ("internalBpm", internalBpm.load (std::memory_order_relaxed), nullptr);

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

    if (state.hasProperty ("internalBpm"))
        internalBpm.store (juce::jlimit (20.0, 300.0, (double) state.getProperty ("internalBpm")),
                           std::memory_order_relaxed);

    // Restore the structural Project, if this preset carries one (older presets
    // without it just keep the current project).
    const juce::var projVar = juce::JSON::parse (state.getProperty ("projectJson").toString());
    if (! projVar.isObject())
        return;

    auto migrated = sila::engine::projectFromVar (projVar);
    // Phase 7c migration: pre-v6 presets had no per-pattern mix (per-track params
    // were global APVTS state, just restored above). Replicate that live mix into
    // every authored slot's kit so switching patterns doesn't reset levels/filter.
    // Done on the mutable project BEFORE publishing — one setProject, no extra swap.
    if ((int) projVar.getProperty ("schema_version", 0) < 6)
        for (int s = 0; s < sila::engine::PatternBank::kNumSlots; ++s)
            if (! migrated.patternBank.kits[(size_t) s].empty())
                captureLaneParams (migrated, s);

    auto proj = std::make_shared<const sila::engine::Project> (std::move (migrated));
    // Build the sampler bank now (WindowedSinc resamples each source file to the
    // current device rate). If this runs before prepareToPlay, sampleRate is the
    // default; prepareToPlay's rebuildSamplerBankForRate then re-resamples from
    // the same SampleRef paths to the real rate — self-healing.
    auto set = std::make_shared<const SamplerSet> (buildBankForProject (*proj, sampleRate));
    setProject (std::move (proj), std::move (set));
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
