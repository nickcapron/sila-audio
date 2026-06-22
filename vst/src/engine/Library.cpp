#include "engine/Library.h"
#include <algorithm>
#include <array>
#include <initializer_list>
#include <set>

namespace sila::engine
{
namespace
{
const std::array<const char*, 59> kCanonical = {
    "01. Kick", "02. Snare", "03. Clap", "04. Hi-Hat Closed",
    "05. Hi-Hat Open", "06. Cymbal", "07. Ride", "08. Crash",
    "09. Tom", "10. Rimshot", "11. Sidestick", "12. Cowbell",
    "13. Conga", "14. Bongo", "15. Tambourine", "16. Shaker",
    "17. Cabasa", "18. Maracas", "19. Triangle", "20. Electronic Perc",
    "21. Bass - Sub", "22. Bass - Synth", "23. Bass - 808",
    "24. Bass - Acoustic", "25. Lead - Saw", "26. Lead - Square",
    "27. Lead - Pluck", "28. Lead - Acid", "29. Pad - Warm",
    "30. Pad - Strings", "31. Pad - Atmosphere", "32. Pad - Choir",
    "33. Keys - Piano", "34. Keys - Electric Piano", "35. Keys - Organ",
    "36. Keys - Rhodes", "37. Stab", "38. Brass",
    "39. Strings - Solo", "40. Strings - Ensemble",
    "41. Pluck - Guitar", "42. Pluck - Synth", "43. Pluck - Harp",
    "44. Arp", "45. Drone", "46. Texture", "47. Basic Waveforms",
    "48. Vocal - Chops", "49. Vocal - One Shots", "50. Vocal - Phrases",
    "51. Vocal - Harmony", "52. Vocal - Ad Libs",
    "53. FX - Rise", "54. FX - Fall", "55. FX - Impact",
    "56. FX - Noise", "57. FX - Glitch", "58. Foley",
    "59. Field Recording",
};

// DAW/format wrapper folders — looked through transparently when grouping.
const juce::StringArray kDawWrappers {
    "wav", "audio", "ableton", "kontakt", "battery", "logic", "maschine",
    "sfz", "reason", "mpc", "ni", "native instruments"
};

bool isBpmFolder (const juce::String& nameLower)
{
    // "120bpm", "130 bpm" — digits, optional space, then "bpm".
    auto s = nameLower.trim();
    if (! s.endsWith ("bpm")) return false;
    s = s.dropLastCharacters (3).trim();
    return s.isNotEmpty() && s.containsOnly ("0123456789");
}

bool isKeyFolder (const juce::String& name)
{
    // Am, CM, C#, Db, C#m, Dbm, "D Major", "C# minor" — needs an accidental,
    // a mode letter, or a written-out mode word (a bare "A" stays a group).
    auto s = name.trim().toLowerCase();
    if (s.isEmpty()) return false;
    const juce::juce_wchar root = s[0];
    if (root < 'a' || root > 'g') return false;
    auto rest = s.substring (1).trim();
    if (rest.isEmpty()) return false;                      // bare note letter -> not a key folder

    // Strip a leading accidental.
    bool hadAccidental = false;
    if (rest.startsWithChar ('#') || rest.startsWithChar ('b'))
    {
        hadAccidental = true;
        rest = rest.substring (1).trim();
    }
    if (rest.isEmpty()) return hadAccidental;              // "C#", "Db"
    if (rest == "m" || rest == "maj" || rest == "min"
        || rest == "major" || rest == "minor")
        return true;                                       // Am, C#m, "D Major"
    return false;
}

bool isWrapperOrMeta (const juce::String& part)
{
    const auto lower = part.toLowerCase();
    if (kDawWrappers.contains (lower) || isBpmFolder (lower) || isKeyFolder (part))
        return true;
    // Multi-word format folders: "Logic EXS", "Reason NN-XT", "Ableton Live" —
    // match on the first token so they're looked through like single-word wrappers.
    const auto firstTok = lower.upToFirstOccurrenceOf (" ", false, false);
    return firstTok != lower && kDawWrappers.contains (firstTok);
}

// First meaningful directory in `relParts` (excluding the filename), skipping
// wrappers and BPM/key folders; falls back to `rootName` for flat files.
juce::String groupName (const juce::StringArray& relParts, const juce::String& rootName)
{
    for (int i = 0; i < relParts.size() - 1; ++i)   // all but the filename
        if (! isWrapperOrMeta (relParts[i]))
            return relParts[i];
    return rootName;
}

juce::Array<juce::File> findAudioFiles (const juce::File& root)
{
    juce::Array<juce::File> out;
    for (const auto& f : root.findChildFiles (juce::File::findFiles, true /*recursive*/))
        if (isLibraryAudioFile (f))
            out.add (f);
    out.sort();
    return out;
}

juce::StringArray relParts (const juce::File& file, const juce::File& root)
{
    juce::StringArray parts;
    parts.addTokens (file.getRelativePathFrom (root).replaceCharacter ('\\', '/'), "/", "");
    parts.removeEmptyStrings();
    return parts;
}

// ── Mapper (port of mapper.py) ──────────────────────────────────────────────
juce::String norm (const juce::String& t)
{
    return t.toLowerCase().replaceCharacter ('-', ' ').replaceCharacter ('_', ' ')
            .replaceCharacter ('.', ' ');
}

bool has (const juce::String& t, std::initializer_list<const char*> subs)
{
    for (auto* s : subs) if (t.contains (s)) return true;
    return false;
}

// Whole-word match: the word isn't flanked by other a-z letters.
bool word (const juce::String& t, std::initializer_list<const char*> words)
{
    for (auto* w : words)
    {
        const juce::String ww (w);
        int from = 0;
        for (;;)
        {
            const int idx = t.indexOf (from, ww);
            if (idx < 0) break;
            const bool leftOk  = idx == 0 || ! juce::CharacterFunctions::isLetter (t[idx - 1]);
            const int  after   = idx + ww.length();
            const bool rightOk = after >= t.length() || ! juce::CharacterFunctions::isLetter (t[after]);
            if (leftOk && rightOk) return true;
            from = idx + 1;
        }
    }
    return false;
}

juce::String suggestFromText (const juce::String& text)
{
    const auto t = norm (text);

    // Drums — order matters (specific before generic).
    if (has (t, {"kick", "bass drum", "bassdrum"}) || word (t, {"bd"})) return "01. Kick";
    if (has (t, {"snare"}) || word (t, {"sd"}))                          return "02. Snare";
    if (has (t, {"clap"}))                                               return "03. Clap";
    if (has (t, {"closed hat", "hat closed", "chh"}) || word (t, {"ch"})) return "04. Hi-Hat Closed";
    if (has (t, {"open hat", "hat open", "ohh"}) || word (t, {"oh"}))    return "05. Hi-Hat Open";
    if (has (t, {"hihat", "hi hat", "hat"}) || word (t, {"hh"}))         return "04. Hi-Hat Closed";
    if (has (t, {"cymbal"}))                                             return "06. Cymbal";
    if (has (t, {"ride"}))                                               return "07. Ride";
    if (has (t, {"crash"}))                                              return "08. Crash";
    if (has (t, {"tom"}))                                                return "09. Tom";
    if (has (t, {"rim", "rimshot"}))                                     return "10. Rimshot";
    if (has (t, {"sidestick"}))                                          return "11. Sidestick";
    if (has (t, {"cowbell"}))                                            return "12. Cowbell";
    if (has (t, {"conga"}))                                              return "13. Conga";
    if (has (t, {"bongo"}))                                              return "14. Bongo";
    if (has (t, {"tamb", "tambourine"}))                                 return "15. Tambourine";
    if (has (t, {"shaker"}))                                             return "16. Shaker";
    if (has (t, {"cabasa"}))                                             return "17. Cabasa";
    if (has (t, {"maracas"}))                                            return "18. Maracas";
    if (has (t, {"triangle"}))                                           return "19. Triangle";
    if (has (t, {"perc"}))                                               return "20. Electronic Perc";

    // Synths / instruments — "bass" after the kick checks so it routes here.
    if (has (t, {"bass"}))                                               return "21. Bass - Sub";
    if (has (t, {"lead"}))                                               return "25. Lead - Saw";
    if (has (t, {"pad"}))                                                return "29. Pad - Warm";
    if (has (t, {"piano"}))                                              return "33. Keys - Piano";
    if (has (t, {"organ"}))                                              return "35. Keys - Organ";
    if (has (t, {"keys", "keyboard"}))                                   return "33. Keys - Piano";
    if (word (t, {"key"}))                                               return "33. Keys - Piano";
    if (has (t, {"stab"}))                                               return "37. Stab";
    if (has (t, {"brass", "horn"}))                                      return "38. Brass";
    if (has (t, {"string", "violin", "cello", "viola"}))                return "39. Strings - Solo";
    if (has (t, {"pluck"}))                                              return "41. Pluck - Guitar";
    if (has (t, {"arp"}))                                                return "44. Arp";
    if (has (t, {"drone"}))                                              return "45. Drone";

    if (has (t, {"vocal", "vox", "voice", "choir", "sing"}))            return "48. Vocal - Chops";

    if (has (t, {"riser", "rise"}))                                      return "53. FX - Rise";
    if (has (t, {"fall", "down"}))                                       return "54. FX - Fall";
    if (has (t, {"impact", "hit"}))                                      return "55. FX - Impact";
    if (has (t, {"noise"}))                                              return "56. FX - Noise";
    if (has (t, {"glitch"}))                                             return "57. FX - Glitch";

    // Basic waveforms — last, so any specific instrument rule above wins first
    // (a "Saw Bass" folder routes to bass; a bare "Pulse"/"Saw" oscillator here).
    if (has (t, {"waveform", "basic wave"})
        || word (t, {"saw", "square", "sine", "triangle", "pulse"}))     return "47. Basic Waveforms";

    return {};
}

// Smart per-file classifier: the curated OUTER folders first (so a synth model
// name in a patch/file — e.g. "Clav TOM 1501" — can't false-match a drum), then
// the filename. Returns "" (uncategorized) when nothing matches.
juce::String classifyFile (const juce::StringArray& relParts)
{
    for (int i = 0; i < relParts.size() - 1; ++i)       // folders, outermost -> inner
    {
        if (isWrapperOrMeta (relParts[i])) continue;
        const auto c = suggestFromText (relParts[i]);
        if (c.isNotEmpty()) return c;
    }
    const auto stem = relParts[relParts.size() - 1].upToLastOccurrenceOf (".", false, false);
    return suggestFromText (stem);
}
} // namespace

const char* const kUncategorizedGroup = "Uncategorized";

const juce::StringArray& canonicalCategories()
{
    static const juce::StringArray cats = []
    {
        juce::StringArray s;
        for (auto* c : kCanonical) s.add (c);
        return s;
    }();
    return cats;
}

bool isCanonicalCategory (const juce::String& name) { return canonicalCategories().contains (name); }

bool isLibraryAudioFile (const juce::File& f)
{
    const auto ext = f.getFileExtension().toLowerCase();
    return ext == ".wav" || ext == ".aiff" || ext == ".aif";
}

juce::File safeChild (const juce::File& base, const juce::String& relative, bool& ok)
{
    const auto baseFull = base.getFullPathName();
    const auto child    = base.getChildFile (relative);
    // isAChildOf is false for base itself; allow equality too (rare but harmless).
    ok = child == base || child.isAChildOf (base)
         || child.getFullPathName().startsWith (baseFull + juce::File::getSeparatorString());
    return ok ? child : juce::File();
}

juce::String sanitizeLibraryFilename (const juce::String& name)
{
    // Split on the last '.' ourselves (the input is a bare filename, not a path).
    const int dot = name.lastIndexOfChar ('.');
    const auto ext  = dot > 0 ? name.substring (dot).toLowerCase() : juce::String();
    auto stem = dot > 0 ? name.substring (0, dot) : name;

    stem = stem.replaceCharacter (' ', '_').retainCharacters (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-.");
    while (stem.startsWithChar ('.') || stem.startsWithChar ('_')) stem = stem.substring (1);
    while (stem.endsWithChar ('.')   || stem.endsWithChar ('_'))   stem = stem.dropLastCharacters (1);
    if (stem.isEmpty()) stem = "sample";
    if (stem.length() > 64) stem = stem.substring (0, 64);
    return stem + ext;
}

juce::String sanitizePackName (const juce::String& name)
{
    auto s = name.replaceCharacter (' ', '_').retainCharacters (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-.");
    while (s.startsWithChar ('.') || s.startsWithChar ('_')) s = s.substring (1);
    while (s.endsWithChar ('.')   || s.endsWithChar ('_'))   s = s.dropLastCharacters (1);
    if (s.length() > 64) s = s.substring (0, 64);

    static const juce::StringArray reserved {
        "CON", "PRN", "AUX", "NUL",
        "COM1","COM2","COM3","COM4","COM5","COM6","COM7","COM8","COM9",
        "LPT1","LPT2","LPT3","LPT4","LPT5","LPT6","LPT7","LPT8","LPT9" };
    if (reserved.contains (s.upToFirstOccurrenceOf (".", false, false).toUpperCase()))
        return {};
    return s;
}

void ensureMySamples (const juce::File& libraryRoot)
{
    auto mySamples = libraryRoot.getChildFile ("My Samples");
    mySamples.createDirectory();
    for (const auto& cat : canonicalCategories())
        mySamples.getChildFile (cat).createDirectory();
}

juce::String suggestCategory (const juce::String& groupNameIn, const juce::StringArray& filenames)
{
    auto result = suggestFromText (groupNameIn);
    if (result.isNotEmpty()) return result;
    for (int i = 0; i < juce::jmin (10, filenames.size()); ++i)
    {
        result = suggestFromText (filenames[i]);
        if (result.isNotEmpty()) return result;
    }
    return {};
}

ScanResult scanFolder (const juce::File& source, bool smart)
{
    ScanResult res;
    res.sourcePath = source.getFullPathName();
    if (! source.isDirectory()) return res;

    const auto rootName = source.getFileName();
    const auto files    = findAudioFiles (source);
    res.totalFiles      = files.size();

    if (smart)
    {
        // Group by the CLASSIFIED category (per file), not the source folder.
        std::map<juce::String, int> byCat;
        for (const auto& f : files)
        {
            auto cat = classifyFile (relParts (f, source));
            byCat[cat.isNotEmpty() ? cat : juce::String (kUncategorizedGroup)]++;
        }
        // Emit in canonical-category order; Uncategorized last. The group name IS
        // the category, so its suggestion pre-selects that same category.
        for (const auto& cat : canonicalCategories())
        {
            auto it = byCat.find (cat);
            if (it != byCat.end()) res.groups.push_back ({ cat, it->second, cat });
        }
        auto un = byCat.find (kUncategorizedGroup);
        if (un != byCat.end()) res.groups.push_back ({ kUncategorizedGroup, un->second, juce::String() });
        return res;
    }

    // Folder mode: one group per source folder. Preserve first-seen order, but
    // collect filenames so the per-group suggestion can sniff them.
    std::vector<juce::String> order;
    std::map<juce::String, juce::StringArray> byGroup;   // group -> filenames
    for (const auto& f : files)
    {
        const auto g = groupName (relParts (f, source), rootName);
        if (byGroup.find (g) == byGroup.end()) order.push_back (g);
        byGroup[g].add (f.getFileName());
    }

    std::sort (order.begin(), order.end(),
               [] (const juce::String& a, const juce::String& b) { return a.compareNatural (b) < 0; });
    for (const auto& g : order)
    {
        const auto& names = byGroup[g];
        res.groups.push_back ({ g, names.size(), suggestCategory (g, names) });
    }
    return res;
}

ImportResult executeImport (const juce::File& source,
                            const juce::String& packName,
                            const std::map<juce::String, juce::String>& mappings,
                            const juce::File& libraryRoot,
                            bool smart)
{
    ImportResult r;
    if (! source.isDirectory()) { r.error = "Source directory not found"; return r; }

    const auto pack = sanitizePackName (packName);
    if (pack.isEmpty()) { r.error = "Pack name is empty after sanitization"; return r; }

    bool ok = false;
    const auto packDir = safeChild (libraryRoot, pack, ok);
    if (! ok) { r.error = "Invalid pack name"; return r; }

    const auto rootName = source.getFileName();
    const auto files    = findAudioFiles (source);
    std::set<juce::String> categoriesCreated;

    for (const auto& f : files)
    {
        // The mapping key must be computed the same way the scan grouped: by
        // classified category (smart) or by source folder (folder mode).
        const auto rp = relParts (f, source);
        juce::String key;
        if (smart)  { auto c = classifyFile (rp); key = c.isNotEmpty() ? c : juce::String (kUncategorizedGroup); }
        else        { key = groupName (rp, rootName); }
        const auto it = mappings.find (key);
        if (it == mappings.end() || ! isCanonicalCategory (it->second)) { r.skipped++; continue; }

        bool catOk = false;
        const auto catDir = safeChild (packDir, it->second, catOk);
        if (! catOk) { r.skipped++; continue; }
        if (! catDir.isDirectory())
        {
            catDir.createDirectory();
            categoriesCreated.insert (it->second);
        }

        const auto dest = catDir.getChildFile (sanitizeLibraryFilename (f.getFileName()));
        if (dest.existsAsFile()) { r.skipped++; continue; }     // never overwrite
        if (f.copyFileTo (dest)) r.imported++;
        else                     r.skipped++;
    }

    r.categoriesCreated = (int) categoriesCreated.size();
    return r;
}
} // namespace sila::engine
