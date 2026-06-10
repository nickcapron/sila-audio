#pragma once
#include <juce_core/juce_core.h>
#include "engine/Project.h"
#include <vector>

// Port of ../../sila/export/digitakt.py.
// Transcodes every sample referenced by the project to Elektron-Transfer-ready
// WAVs: 48 kHz / 16-bit / mono PCM, sanitized 16-char filenames, flat output
// folder. This is a *sample packager*, not a pattern bounce — the Digitakt is a
// sampler, so it receives the individual one-shots. Source files are re-read
// from disk (not the device-rate engine buffers) so each is resampled exactly
// once, straight to 48 kHz, independent of the host's device rate.
namespace sila::engine
{
struct ExportWarning
{
    juce::String path;
    juce::String reason;   // "exceeds_duration" | "exceeds_size"
    double       value = 0.0;
};

struct ExportResult
{
    int exported = 0;
    int renamed  = 0;      // files whose name was sanitized / changed
    int skipped  = 0;      // missing/unreadable/blocked — reported, not silently dropped
    std::vector<ExportWarning> warnings;

    juce::String summary() const;   // port of export_result_summary()
};

// Resolve relative paths against `libraryRoot` (the plugin's equivalent of the
// Python samples_dir); absolute paths are used as-is. Writes into `outputDir`.
ExportResult exportForDigitakt (const Project& project,
                                const juce::File& libraryRoot,
                                const juce::File& outputDir);

// ASCII-safe, 16-char filename (port of security.py::sanitize_filename).
juce::String sanitizeFilename (const juce::String& name);
} // namespace sila::engine
