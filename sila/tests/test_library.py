"""
Tests for the sample library browser.

Uses tmp_path to avoid touching ~/SILA/library/ on disk.
"""
from pathlib import Path

import pytest

from sila.library.browser import (
    CANONICAL_CATEGORIES,
    MY_SAMPLES_NAME,
    copy_to_project,
    ensure_my_samples,
    get_library_tree,
    resolve_library_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("sila.library.browser.LIBRARY_ROOT", tmp_path)


# ---------------------------------------------------------------------------
# ensure_my_samples
# ---------------------------------------------------------------------------

def test_ensure_creates_canonical_dirs(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    ensure_my_samples()
    my_samples = tmp_path / MY_SAMPLES_NAME
    assert my_samples.is_dir()
    for cat in CANONICAL_CATEGORIES:
        assert (my_samples / cat).is_dir(), f"missing canonical category: {cat}"


def test_ensure_all_59_categories(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    ensure_my_samples()
    created = {d.name for d in (tmp_path / MY_SAMPLES_NAME).iterdir() if d.is_dir()}
    assert len(created) == 59


def test_ensure_is_idempotent(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    ensure_my_samples()
    ensure_my_samples()  # must not raise
    assert (tmp_path / MY_SAMPLES_NAME).is_dir()


# ---------------------------------------------------------------------------
# get_library_tree
# ---------------------------------------------------------------------------

def test_tree_empty_when_library_missing(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path / "nonexistent")
    assert get_library_tree() == []


def test_tree_empty_pack_list_when_root_is_empty(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    assert get_library_tree() == []


def test_tree_returns_pack_with_samples(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    cat = tmp_path / "My Pack" / "Kicks"
    cat.mkdir(parents=True)
    (cat / "kick.wav").write_bytes(b"\x00" * 100)

    tree = get_library_tree()
    assert len(tree) == 1
    pack = tree[0]
    assert pack.name == "My Pack"
    assert len(pack.categories) == 1
    assert pack.categories[0].name == "Kicks"
    assert len(pack.categories[0].samples) == 1
    s = pack.categories[0].samples[0]
    assert s.name == "kick"
    assert s.filename == "kick.wav"
    assert s.size_bytes == 100
    assert s.path == "My Pack/Kicks/kick.wav"


def test_tree_skips_empty_categories(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    # Category with no audio files
    (tmp_path / "Pack" / "Empty Cat").mkdir(parents=True)
    (tmp_path / "Pack" / "Empty Cat" / "readme.txt").write_text("hi")
    # Category with one audio file
    filled = tmp_path / "Pack" / "Kicks"
    filled.mkdir()
    (filled / "kick.wav").write_bytes(b"\x00" * 50)

    tree = get_library_tree()
    assert len(tree) == 1
    cats = tree[0].categories
    assert len(cats) == 1  # empty cat omitted
    assert cats[0].name == "Kicks"


def test_my_samples_is_first_regardless_of_sort_order(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    # "AAAA" sorts before "My Samples" alphabetically — must still come second.
    aaaa = tmp_path / "AAAA Pack" / "Cat"
    aaaa.mkdir(parents=True)
    (aaaa / "snd.wav").write_bytes(b"\x00" * 10)
    ensure_my_samples()
    (tmp_path / MY_SAMPLES_NAME / "01. Kick" / "kick.wav").write_bytes(b"\x00" * 10)

    tree = get_library_tree()
    assert tree[0].name == MY_SAMPLES_NAME
    assert tree[1].name == "AAAA Pack"


def test_tree_recognises_aiff_extension(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    cat = tmp_path / "Pack" / "Cat"
    cat.mkdir(parents=True)
    (cat / "snare.aiff").write_bytes(b"\x00" * 20)

    tree = get_library_tree()
    assert tree[0].categories[0].samples[0].filename == "snare.aiff"


def test_tree_ignores_dotfiles(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    pack_dir = tmp_path / ".hidden_pack"
    pack_dir.mkdir()
    (pack_dir / "Cat").mkdir()
    (pack_dir / "Cat" / "snd.wav").write_bytes(b"\x00" * 10)

    tree = get_library_tree()
    assert tree == []


# ---------------------------------------------------------------------------
# resolve_library_path
# ---------------------------------------------------------------------------

def test_resolve_blocks_traversal(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        resolve_library_path("../../../etc/passwd")


# ---------------------------------------------------------------------------
# copy_to_project
# ---------------------------------------------------------------------------

def test_copy_places_file_in_samples_dir(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    src = tmp_path / "Pack" / "Cat"
    src.mkdir(parents=True)
    (src / "kick.wav").write_bytes(b"audio_data")

    dest = tmp_path / "project_samples"
    dest.mkdir()

    filename = copy_to_project("Pack/Cat/kick.wav", dest)
    assert filename == "kick.wav"
    assert (dest / "kick.wav").read_bytes() == b"audio_data"


def test_copy_does_not_overwrite_existing(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    src = tmp_path / "Pack" / "Cat"
    src.mkdir(parents=True)
    (src / "kick.wav").write_bytes(b"new_audio")

    dest = tmp_path / "project_samples"
    dest.mkdir()
    (dest / "kick.wav").write_bytes(b"existing")

    copy_to_project("Pack/Cat/kick.wav", dest)
    assert (dest / "kick.wav").read_bytes() == b"existing"


def test_copy_raises_for_missing_file(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    dest = tmp_path / "project_samples"
    dest.mkdir()

    with pytest.raises(FileNotFoundError):
        copy_to_project("Pack/Cat/ghost.wav", dest)
