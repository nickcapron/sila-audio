#pragma once
#include <juce_core/juce_core.h>
#include <vector>
#include <map>

// Port of the Python sample library tooling:
//   ../../sila/library/browser.py        (canonical categories, ensure My Samples)
//   ../../sila/import_tool/scanner.py     (recursive scan + grouping + import)
//   ../../sila/import_tool/mapper.py      (keyword category suggester)
//   ../../sila/security.py                (path-traversal guard + filename safety)
//
// Pure logic only — JSON (juce::var) conversion lives in PluginEditor. All paths
// are resolved against the plugin's library root (~/SILA/library equivalent).
namespace sila::engine
{
// The 59 SILA canonical categories (the only sub-folders an import may create).
const juce::StringArray& canonicalCategories();
bool isCanonicalCategory (const juce::String& name);

// True for .wav/.aiff/.aif (the formats the Sampler decodes).
bool isLibraryAudioFile (const juce::File&);

// ── Security / filename safety (port of security.py) ────────────────────────
// Resolve `relative` under `base` and verify it stays inside `base`. Returns a
// non-existent File and sets `ok=false` on a traversal attempt.
juce::File safeChild (const juce::File& base, const juce::String& relative, bool& ok);
// Spaces -> '_', strip non [A-Za-z0-9_-.], trim ._ ends, stem <= 64, keep ext.
juce::String sanitizeLibraryFilename (const juce::String& name);
// Same rules, 64-char limit, blocks Windows reserved device names. "" if empty.
juce::String sanitizePackName (const juce::String& name);

// Create ~/SILA/library/My Samples/<every canonical category>/. Idempotent.
void ensureMySamples (const juce::File& libraryRoot);

// ── Scanner + mapper ────────────────────────────────────────────────────────
struct ScanGroup
{
    juce::String name;          // logical group (folder) name
    int          fileCount = 0;
    juce::String suggestion;    // canonical category, or "" when unknown
};

struct ScanResult
{
    std::vector<ScanGroup> groups;
    int                    totalFiles = 0;
    juce::String           sourcePath;
};

// Recursively scan `source` and produce the groups the import UI maps to
// categories. `smart=false` groups by source folder (one row per folder).
// `smart=true` classifies every file individually (curated outer folder first,
// then filename) and groups by the RESULTING category — so a flat folder mixing
// kicks/snares/hats explodes into the right categories, and unmatched files land
// in an "Uncategorized" group. Each group's `suggestion` pre-selects the dropdown.
ScanResult scanFolder (const juce::File& source, bool smart = false);

// The group name used for files the smart classifier can't place.
extern const char* const kUncategorizedGroup;

// Best canonical category for a group name (falls back to scanning filenames),
// or "" when no confident match.
juce::String suggestCategory (const juce::String& groupName, const juce::StringArray& filenames);

// ── Import ──────────────────────────────────────────────────────────────────
struct ImportResult
{
    int imported = 0;
    int skipped  = 0;
    int categoriesCreated = 0;
    juce::String error;   // non-empty on failure
};

// Copy scanned files into <libraryRoot>/<packName>/<category>/. `mappings` maps
// group name -> canonical category; groups absent from it are skipped. Never
// overwrites an existing destination file. `smart` must match the scan mode the
// `mappings` keys came from (folder names vs. classified category names).
ImportResult executeImport (const juce::File& source,
                            const juce::String& packName,
                            const std::map<juce::String, juce::String>& mappings,
                            const juce::File& libraryRoot,
                            bool smart = false);
} // namespace sila::engine
