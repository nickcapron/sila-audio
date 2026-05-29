"""
Tests for the sample import tool: scanner grouping logic, mapper suggestions,
execute_import file copying, and sanitize_library_filename.
"""
from pathlib import Path

import pytest

from sila.import_tool.mapper import suggest_category
from sila.import_tool.scanner import (
    _get_group_name,
    _is_bpm_or_key_folder,
    execute_import,
    scan_folder,
)
from sila.security import sanitize_library_filename


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wav(path: Path, content: bytes = b"\x00" * 44) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _make_lib(tmp_path: Path) -> Path:
    """Return a fresh library root path (execute_import takes it as a parameter)."""
    return tmp_path / "library"


# ---------------------------------------------------------------------------
# _is_bpm_or_key_folder
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["120BPM", "130 BPM", "90bpm", "100Bpm"])
def test_bpm_folder_detected(name):
    assert _is_bpm_or_key_folder(name)


@pytest.mark.parametrize("name", ["Am", "C#", "Dbm", "F# Major", "DM", "CM"])
def test_key_folder_detected(name):
    assert _is_bpm_or_key_folder(name)


@pytest.mark.parametrize("name", ["Kicks", "Bass", "A", "Beats", "808", "FX"])
def test_regular_folder_not_flagged(name):
    assert not _is_bpm_or_key_folder(name)


# ---------------------------------------------------------------------------
# _get_group_name
# ---------------------------------------------------------------------------

def test_flat_file_groups_to_root_name():
    assert _get_group_name(("kick.wav",), "MyPack") == "MyPack"


def test_single_subfolder_used_as_group():
    assert _get_group_name(("Kicks", "kick.wav"), "MyPack") == "Kicks"


def test_daw_wrapper_skipped():
    assert _get_group_name(("WAV", "Kicks", "kick.wav"), "MyPack") == "Kicks"


def test_multiple_daw_wrappers_skipped():
    assert _get_group_name(("Ableton", "WAV", "Kicks", "kick.wav"), "MyPack") == "Kicks"


def test_bpm_subfolder_skipped():
    assert _get_group_name(("Kicks", "120BPM", "kick.wav"), "MyPack") == "Kicks"


def test_key_subfolder_skipped():
    assert _get_group_name(("Leads", "Am", "lead.wav"), "MyPack") == "Leads"


def test_only_wrappers_and_bpm_falls_back_to_root():
    # WAV/120BPM/kick.wav → no meaningful dir → root name
    assert _get_group_name(("WAV", "120BPM", "kick.wav"), "MyPack") == "MyPack"


def test_native_instruments_wrapper_skipped():
    assert _get_group_name(("Native Instruments", "Kicks", "kick.wav"), "P") == "Kicks"


# ---------------------------------------------------------------------------
# scan_folder
# ---------------------------------------------------------------------------

def test_scan_flat_folder(tmp_path):
    pack = tmp_path / "MyPack"
    _wav(pack / "kick.wav")
    _wav(pack / "snare.wav")

    result = scan_folder(str(pack))
    assert result.total_files == 2
    assert len(result.groups) == 1
    assert result.groups[0].name == "MyPack"
    assert result.groups[0].file_count == 2


def test_scan_subfolder_grouping(tmp_path):
    pack = tmp_path / "Pack"
    _wav(pack / "Kicks" / "kick.wav")
    _wav(pack / "Snares" / "snare.wav")
    _wav(pack / "Snares" / "snare2.wav")

    result = scan_folder(str(pack))
    assert result.total_files == 3
    names = {g.name for g in result.groups}
    assert names == {"Kicks", "Snares"}
    snare_group = next(g for g in result.groups if g.name == "Snares")
    assert snare_group.file_count == 2


def test_scan_skips_daw_wrapper(tmp_path):
    pack = tmp_path / "Pack"
    _wav(pack / "WAV" / "Kicks" / "kick.wav")

    result = scan_folder(str(pack))
    assert result.groups[0].name == "Kicks"


def test_scan_skips_bpm_subfolder(tmp_path):
    pack = tmp_path / "Pack"
    _wav(pack / "Kicks" / "120BPM" / "kick.wav")

    result = scan_folder(str(pack))
    assert result.groups[0].name == "Kicks"


def test_scan_ignores_non_audio_files(tmp_path):
    pack = tmp_path / "Pack"
    _wav(pack / "Kicks" / "kick.wav")
    (pack / "Kicks" / "readme.txt").write_text("ignore me")

    result = scan_folder(str(pack))
    assert result.total_files == 1


def test_scan_total_files_count(tmp_path):
    pack = tmp_path / "Pack"
    for i in range(5):
        _wav(pack / "Cat" / f"snd{i}.wav")

    result = scan_folder(str(pack))
    assert result.total_files == 5


def test_scan_raises_on_missing_path(tmp_path):
    with pytest.raises(ValueError, match="Not a directory"):
        scan_folder(str(tmp_path / "ghost"))


def test_scan_suggestion_attached(tmp_path):
    pack = tmp_path / "Pack"
    _wav(pack / "Kicks" / "kick.wav")

    result = scan_folder(str(pack))
    assert result.groups[0].suggestion == "01. Kick"


def test_scan_unknown_group_has_no_suggestion(tmp_path):
    pack = tmp_path / "Pack"
    _wav(pack / "XYZ_Unknown" / "snd.wav")

    result = scan_folder(str(pack))
    assert result.groups[0].suggestion is None


# ---------------------------------------------------------------------------
# execute_import
# ---------------------------------------------------------------------------

def test_execute_copies_to_correct_path(tmp_path):
    lib = _make_lib(tmp_path)
    pack = tmp_path / "Pack"
    _wav(pack / "Kicks" / "kick.wav", content=b"audio")

    result = execute_import(str(pack), "My Pack", {"Kicks": "01. Kick"}, lib)

    assert result.imported == 1
    assert result.skipped == 0
    dest = lib / "My_Pack" / "01. Kick" / "kick.wav"
    assert dest.exists()
    assert dest.read_bytes() == b"audio"


def test_execute_skips_unmapped_groups(tmp_path):
    lib = _make_lib(tmp_path)
    pack = tmp_path / "Pack"
    _wav(pack / "Kicks" / "kick.wav")
    _wav(pack / "Unknown" / "snd.wav")

    result = execute_import(str(pack), "P", {"Kicks": "01. Kick"}, lib)

    assert result.imported == 1
    assert result.skipped == 1


def test_execute_does_not_overwrite(tmp_path):
    lib = _make_lib(tmp_path)
    pack = tmp_path / "Pack"
    _wav(pack / "Kicks" / "kick.wav", content=b"new")

    dest_dir = lib / "P" / "01. Kick"
    dest_dir.mkdir(parents=True)
    (dest_dir / "kick.wav").write_bytes(b"existing")

    result = execute_import(str(pack), "P", {"Kicks": "01. Kick"}, lib)

    assert result.skipped == 1
    assert result.imported == 0
    assert (dest_dir / "kick.wav").read_bytes() == b"existing"


def test_execute_counts_categories_created(tmp_path):
    lib = _make_lib(tmp_path)
    pack = tmp_path / "Pack"
    _wav(pack / "Kicks" / "kick.wav")
    _wav(pack / "Snares" / "snare.wav")

    result = execute_import(
        str(pack), "P",
        {"Kicks": "01. Kick", "Snares": "02. Snare"},
        lib,
    )

    assert result.categories_created == 2


def test_execute_sanitizes_pack_name(tmp_path):
    lib = _make_lib(tmp_path)
    pack = tmp_path / "Pack"
    _wav(pack / "Kicks" / "kick.wav")

    execute_import(str(pack), "My 808 Pack!", {"Kicks": "01. Kick"}, lib)

    assert (lib / "My_808_Pack").is_dir()


def test_execute_raises_on_empty_pack_name(tmp_path):
    lib = _make_lib(tmp_path)
    with pytest.raises(ValueError, match="empty after sanitization"):
        execute_import(str(tmp_path), "!!!!", {}, lib)


def test_execute_safe_path_blocks_traversal_in_category(tmp_path):
    """safe_path() inside execute_import must block category-name traversal."""
    lib = _make_lib(tmp_path)
    pack = tmp_path / "Pack"
    _wav(pack / "Kicks" / "kick.wav")
    with pytest.raises(ValueError, match="[Tt]raversal"):
        execute_import(str(pack), "P", {"Kicks": "../../../../etc/passwd"}, lib)


# ---------------------------------------------------------------------------
# mapper — suggest_category
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("Kicks",         "01. Kick"),
    ("kick drum",     "01. Kick"),
    ("bass drum",     "01. Kick"),
    ("BD_Hits",       "01. Kick"),
    ("Snares",        "02. Snare"),
    ("SD_Hits",       "02. Snare"),
    ("Claps",         "03. Clap"),
    ("Closed Hats",   "04. Hi-Hat Closed"),
    ("CHH",           "04. Hi-Hat Closed"),
    ("Open Hats",     "05. Hi-Hat Open"),
    ("OHH",           "05. Hi-Hat Open"),
    ("HiHats",        "04. Hi-Hat Closed"),
    ("Bass Lines",    "21. Bass - Sub"),
    ("Lead Synths",   "25. Lead - Saw"),
    ("Pads",          "29. Pad - Warm"),
    ("Piano Hits",    "33. Keys - Piano"),
    ("Organ",         "35. Keys - Organ"),
    ("Stabs",         "37. Stab"),
    ("Brass Hits",    "38. Brass"),
    ("Strings",       "39. Strings - Solo"),
    ("Plucks",        "41. Pluck - Guitar"),
    ("Arps",          "44. Arp"),
    ("Drones",        "45. Drone"),
    ("Vocals",        "48. Vocal - Chops"),
    ("Vox Chops",     "48. Vocal - Chops"),
    ("Risers",        "53. FX - Rise"),
    ("Falls",         "54. FX - Fall"),
    ("Impacts",       "55. FX - Impact"),
    ("Noise",         "56. FX - Noise"),
    ("Glitches",      "57. FX - Glitch"),
])
def test_suggest_category_by_group_name(name, expected):
    assert suggest_category(name) == expected


def test_suggest_unknown_returns_none():
    assert suggest_category("XYZ_Foobarbaz") is None


def test_suggest_bass_drum_before_bass():
    # "bass drum" must map to Kick, not Bass
    assert suggest_category("bass drum") == "01. Kick"
    assert suggest_category("bassdrum") == "01. Kick"


def test_suggest_generic_bass_maps_to_bass_sub():
    assert suggest_category("Bass") == "21. Bass - Sub"


def test_suggest_falls_back_to_filenames():
    # Group name has no match, but filenames contain "kick"
    result = suggest_category("Group_01", filenames=["kick_hard.wav", "kick_soft.wav"])
    assert result == "01. Kick"


def test_suggest_group_name_wins_over_filenames():
    # "Snares" group should map to snare even if filenames say kick
    result = suggest_category("Snares", filenames=["kick.wav"])
    assert result == "02. Snare"


# ---------------------------------------------------------------------------
# sanitize_library_filename
# ---------------------------------------------------------------------------

def test_sanitize_library_filename_basic():
    assert sanitize_library_filename("kick drum.wav") == "kick_drum.wav"


def test_sanitize_library_filename_preserves_extension():
    assert sanitize_library_filename("snare.aiff").endswith(".aiff")


def test_sanitize_library_filename_lowercase_extension():
    assert sanitize_library_filename("kick.WAV").endswith(".wav")


def test_sanitize_library_filename_strips_special():
    assert "!" not in sanitize_library_filename("kick!!.wav")


def test_sanitize_library_filename_64_char_stem():
    long_name = "k" * 80 + ".wav"
    result = sanitize_library_filename(long_name)
    stem = result.rsplit(".", 1)[0]
    assert len(stem) <= 64


def test_sanitize_library_filename_empty_stem_fallback():
    result = sanitize_library_filename("!!.wav")
    assert result == "sample.wav"
