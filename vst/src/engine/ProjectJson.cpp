#include "engine/ProjectJson.h"

namespace sila::engine
{
const char* trigToString (TrigCondition t)
{
    switch (t)
    {
        case TrigCondition::OneIn2:  return "1:2";
        case TrigCondition::OneIn4:  return "1:4";
        case TrigCondition::Fill:    return "fill";
        case TrigCondition::NotFill: return "not_fill";
        case TrigCondition::Always:  break;
    }
    return "always";
}

TrigCondition trigFromString (const juce::String& s)
{
    if (s == "1:2")      return TrigCondition::OneIn2;
    if (s == "1:4")      return TrigCondition::OneIn4;
    if (s == "fill")     return TrigCondition::Fill;
    if (s == "not_fill") return TrigCondition::NotFill;
    return TrigCondition::Always;
}

juce::var stepToVar (const Step& s)
{
    auto* o = new juce::DynamicObject();
    o->setProperty ("active",         s.active);
    o->setProperty ("velocity",       s.velocity);
    o->setProperty ("pitch_offset",   s.pitchOffset);
    o->setProperty ("probability",    s.probability);
    o->setProperty ("trig_condition", juce::String (trigToString (s.trig)));
    o->setProperty ("length",         (double) s.length);
    o->setProperty ("micro_timing",   s.microTiming);

    auto* pl = new juce::DynamicObject();
    if (s.pStart.has_value())     pl->setProperty ("start",     (double) *s.pStart);
    if (s.pEnd.has_value())       pl->setProperty ("end",       (double) *s.pEnd);
    if (s.pCutoff.has_value())    pl->setProperty ("cutoff",    (double) *s.pCutoff);
    if (s.pResonance.has_value()) pl->setProperty ("resonance", (double) *s.pResonance);
    o->setProperty ("p_locks", juce::var (pl));
    return juce::var (o);
}

void applyStepVar (Step& s, const juce::var& v)
{
    if (! v.isObject()) return;
    if (v.hasProperty ("active"))         s.active      = (bool) v["active"];
    if (v.hasProperty ("velocity"))       s.velocity    = (int)  v["velocity"];
    if (v.hasProperty ("pitch_offset"))   s.pitchOffset = (int)  v["pitch_offset"];
    if (v.hasProperty ("probability"))    s.probability = (int)  v["probability"];
    if (v.hasProperty ("length"))         s.length      = (float) (double) v["length"];
    if (v.hasProperty ("micro_timing"))   s.microTiming = (int)  v["micro_timing"];
    if (v.hasProperty ("trig_condition")) s.trig        = trigFromString (v["trig_condition"].toString());
    if (v.hasProperty ("p_locks"))
    {
        const juce::var pl = v["p_locks"];
        s.pStart.reset();
        s.pEnd.reset();
        s.pCutoff.reset();
        s.pResonance.reset();
        if (pl.isObject())
        {
            if (pl.hasProperty ("start"))     s.pStart     = (float) (double) pl["start"];
            if (pl.hasProperty ("end"))       s.pEnd       = (float) (double) pl["end"];
            if (pl.hasProperty ("cutoff"))    s.pCutoff    = (float) (double) pl["cutoff"];
            if (pl.hasProperty ("resonance")) s.pResonance = (float) (double) pl["resonance"];
        }
    }
}

std::vector<SampleRef> parseSampleLayers (const juce::var& samplesArray)
{
    std::vector<SampleRef> out;
    if (auto* arr = samplesArray.getArray())
        for (const auto& lv : *arr)
        {
            if (! lv.isObject()) continue;
            SampleRef r;
            r.path    = lv.getProperty ("path", juce::String()).toString();
            r.velMin  = (int) lv.getProperty ("velocity_min", 0);
            r.velMax  = (int) lv.getProperty ("velocity_max", 127);
            r.start   = (float) (double) lv.getProperty ("start", 0.0);
            r.end     = (float) (double) lv.getProperty ("end", 1.0);
            r.rrGroup = (int) lv.getProperty ("rr_group", 0);
            if (r.path.isNotEmpty()) out.push_back (r);
        }
    return out;
}

static juce::var sampleLayersToVar (const std::vector<SampleRef>& layers)
{
    juce::Array<juce::var> arr;
    for (const auto& layer : layers)
    {
        auto* so = new juce::DynamicObject();
        so->setProperty ("path",         layer.path);
        so->setProperty ("velocity_min", layer.velMin);
        so->setProperty ("velocity_max", layer.velMax);
        so->setProperty ("start",        (double) layer.start);
        so->setProperty ("end",          (double) layer.end);
        so->setProperty ("rr_group",     layer.rrGroup);
        arr.add (juce::var (so));
    }
    return arr;
}

juce::var trackToVar (const Track& t)
{
    auto* o = new juce::DynamicObject();
    o->setProperty ("id",         t.id);
    o->setProperty ("name",       t.name);
    o->setProperty ("color",      juce::String());     // per-track colour: later step
    o->setProperty ("muted",      t.muted);
    o->setProperty ("solo",       t.solo);
    o->setProperty ("volume",     (double) t.volume);
    o->setProperty ("pan",        (double) t.pan);
    o->setProperty ("cutoff",     (double) t.cutoff);
    o->setProperty ("resonance",  (double) t.resonance);
    o->setProperty ("step_count", (int) t.steps.size());

    juce::Array<juce::var> steps;
    for (const auto& s : t.steps) steps.add (stepToVar (s));
    o->setProperty ("steps", steps);
    o->setProperty ("samples", sampleLayersToVar (t.samples));
    return juce::var (o);
}

Track trackFromVar (const juce::var& v)
{
    Track t;
    t.id    = v.getProperty ("id", juce::String()).toString();
    t.name  = v.getProperty ("name", juce::String()).toString();
    t.muted  = (bool) v.getProperty ("muted", false);
    t.solo   = (bool) v.getProperty ("solo", false);
    t.volume    = (float) (double) v.getProperty ("volume", 1.0);
    t.pan       = (float) (double) v.getProperty ("pan", 0.0);
    t.cutoff    = (float) (double) v.getProperty ("cutoff", 1.0);
    t.resonance = (float) (double) v.getProperty ("resonance", 0.0);

    if (auto* steps = v.getProperty ("steps", juce::var()).getArray())
        for (const auto& sv : *steps)
        {
            Step s;
            applyStepVar (s, sv);
            t.steps.push_back (s);
        }
    t.samples = parseSampleLayers (v.getProperty ("samples", juce::var()));
    return t;
}

// Pattern bank: array of slots; each slot is an array (parallel to tracks) of
// per-track step arrays. Empty slot / per-track entry => the Sequencer falls
// back to the track's live steps (see PatternBank in Project.h).
static juce::var patternBankToVar (const PatternBank& bank)
{
    juce::Array<juce::var> slots;
    for (const auto& slot : bank.slots)
    {
        juce::Array<juce::var> perTrack;
        for (const auto& steps : slot)
        {
            juce::Array<juce::var> stepArr;
            for (const auto& s : steps) stepArr.add (stepToVar (s));
            perTrack.add (stepArr);
        }
        slots.add (perTrack);
    }
    return slots;
}

static void patternBankFromVar (PatternBank& bank, const juce::var& v)
{
    auto* slots = v.getArray();
    if (slots == nullptr) return;
    for (int i = 0; i < juce::jmin ((int) slots->size(), PatternBank::kNumSlots); ++i)
    {
        auto& dstSlot = bank.slots[(size_t) i];
        dstSlot.clear();
        if (auto* perTrack = (*slots)[i].getArray())
            for (const auto& trackSteps : *perTrack)
            {
                std::vector<Step> steps;
                if (auto* stepArr = trackSteps.getArray())
                    for (const auto& sv : *stepArr)
                    {
                        Step s;
                        applyStepVar (s, sv);
                        steps.push_back (s);
                    }
                dstSlot.push_back (std::move (steps));
            }
    }
}

juce::var projectToVar (const Project& p)
{
    auto* o = new juce::DynamicObject();
    o->setProperty ("schema_version", kProjectSchemaVersion);

    juce::Array<juce::var> tracks;
    for (const auto& t : p.tracks) tracks.add (trackToVar (t));
    o->setProperty ("tracks", tracks);

    juce::Array<juce::var> chain;
    for (int slot : p.songChain) chain.add (slot);
    o->setProperty ("song_chain", chain);

    o->setProperty ("pattern_bank", patternBankToVar (p.patternBank));
    return juce::var (o);
}

Project projectFromVar (const juce::var& v)
{
    Project p;
    if (auto* tracks = v.getProperty ("tracks", juce::var()).getArray())
        for (const auto& tv : *tracks)
            p.tracks.push_back (trackFromVar (tv));

    if (auto* chain = v.getProperty ("song_chain", juce::var()).getArray())
        for (const auto& sv : *chain)
            p.songChain.push_back ((int) sv);

    patternBankFromVar (p.patternBank, v.getProperty ("pattern_bank", juce::var()));
    return p;
}
} // namespace sila::engine
