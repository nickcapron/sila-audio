#include "engine/DigitaktExport.h"
#include "engine/Resample.h"
#include <juce_audio_formats/juce_audio_formats.h>
#include <set>

namespace sila::engine
{
namespace
{
constexpr double kTargetSr      = 48000.0;
constexpr int    kBitDepth      = 16;
constexpr double kMaxDurationS  = 33.0;
constexpr juce::int64 kMaxSizeB = (juce::int64) 170 * 1024 * 1024;

// Deduplicated sample paths across every pattern's kit (Phase 7: sounds are per-
// pattern now, so export gathers from all slots' kit lanes). Order preserved.
std::vector<juce::String> collectSamplePaths (const Project& project)
{
    std::set<juce::String> seen;
    std::vector<juce::String> paths;
    for (const auto& kit : project.patternBank.kits)
        for (const auto& lane : kit)
            for (const auto& layer : lane.samples)
                if (layer.path.isNotEmpty() && seen.insert (layer.path).second)
                    paths.push_back (layer.path);
    return paths;
}

juce::String uniqueOutputName (const juce::String& baseStem, std::set<juce::String>& used)
{
    const juce::String stem = sanitizeFilename (baseStem);
    juce::String candidate = stem + ".wav";
    if (used.find (candidate) == used.end())
        return candidate;
    // Append a numeric suffix, keeping the whole stem within 16 chars.
    for (int i = 1; i < 1000; ++i)
    {
        const int keep = juce::jmax (1, 14 - juce::String (i).length());
        candidate = stem.substring (0, keep) + juce::String (i) + ".wav";
        if (used.find (candidate) == used.end())
            return candidate;
    }
    return stem + ".wav";   // pathological fallback
}
} // namespace

juce::String sanitizeFilename (const juce::String& name)
{
    juce::String out;
    for (auto c : name.replaceCharacter (' ', '_'))
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')
            || c == '_' || c == '-' || c == '.')
            out += juce::String::charToString (c);

    while (out.isNotEmpty() && (out[0] == '.' || out[0] == '_'))
        out = out.substring (1);
    while (out.isNotEmpty() && (out.getLastCharacter() == '.' || out.getLastCharacter() == '_'))
        out = out.dropLastCharacters (1);

    if (out.isEmpty())
        return "untitled";
    return out.substring (0, 16);
}

ExportResult exportForDigitakt (const Project& project,
                                const juce::File& libraryRoot,
                                const juce::File& outputDir)
{
    ExportResult result;
    outputDir.createDirectory();

    juce::AudioFormatManager formats;
    formats.registerBasicFormats();
    juce::WavAudioFormat wav;
    std::set<juce::String> usedNames;

    for (const auto& relPath : collectSamplePaths (project))
    {
        juce::File src (relPath);
        if (! src.existsAsFile())
            src = libraryRoot.getChildFile (relPath);
        if (! src.existsAsFile())
        {
            result.skipped++;
            continue;
        }

        std::unique_ptr<juce::AudioFormatReader> reader (formats.createReaderFor (src));
        if (reader == nullptr || reader->lengthInSamples <= 0)
        {
            result.skipped++;
            continue;
        }

        // Read + downmix to mono (load_audio_mono_f32 equivalent).
        const int len = (int) reader->lengthInSamples;
        juce::AudioBuffer<float> raw ((int) reader->numChannels, len);
        reader->read (&raw, 0, len, 0, true, true);
        juce::AudioBuffer<float> mono (1, len);
        mono.clear();
        auto* dst = mono.getWritePointer (0);
        for (int ch = 0; ch < raw.getNumChannels(); ++ch)
        {
            const auto* s = raw.getReadPointer (ch);
            for (int i = 0; i < len; ++i)
                dst[i] += s[i];
        }
        if (raw.getNumChannels() > 1)
            mono.applyGain (1.0f / (float) raw.getNumChannels());

        // Single resample straight to 48 kHz from the source rate.
        mono = resampleMonoTo (mono, reader->sampleRate, kTargetSr);
        const int outSamples = mono.getNumSamples();

        // Validate Digitakt limits — warn but still export (user decides).
        const double duration = outSamples / kTargetSr;
        if (duration > kMaxDurationS)
            result.warnings.push_back ({ relPath, "exceeds_duration", duration });
        const juce::int64 estSize = (juce::int64) outSamples * 2 + 44;
        if (estSize > kMaxSizeB)
            result.warnings.push_back ({ relPath, "exceeds_size", (double) estSize });

        const juce::String outName = uniqueOutputName (src.getFileNameWithoutExtension(), usedNames);
        usedNames.insert (outName);
        if (outName != src.getFileName())
            result.renamed++;

        const juce::File outFile = outputDir.getChildFile (outName);
        outFile.deleteFile();
        std::unique_ptr<juce::FileOutputStream> os (outFile.createOutputStream());
        if (os == nullptr)
        {
            result.skipped++;
            continue;
        }
        std::unique_ptr<juce::AudioFormatWriter> writer (
            wav.createWriterFor (os.get(), kTargetSr, 1, kBitDepth, {}, 0));
        if (writer == nullptr)
        {
            result.skipped++;
            continue;
        }
        os.release();   // writer owns the stream now
        writer->writeFromAudioSampleBuffer (mono, 0, outSamples);
        writer.reset();
        result.exported++;
    }

    return result;
}

juce::String ExportResult::summary() const
{
    juce::String s;
    s << exported << " file(s) exported, " << renamed << " renamed, "
      << (int) warnings.size() << " warning(s)";
    if (skipped > 0)
        s << ", " << skipped << " skipped (missing/invalid)";
    s << ".";
    for (const auto& w : warnings)
    {
        if (w.reason == "exceeds_duration")
            s << "\n  WARNING '" << w.path << "': " << juce::String (w.value, 1) << "s exceeds 33s limit";
        else if (w.reason == "exceeds_size")
            s << "\n  WARNING '" << w.path << "': " << juce::String (w.value / (1024.0 * 1024.0), 1)
              << " MB exceeds 170 MB limit";
    }
    return s;
}
} // namespace sila::engine
