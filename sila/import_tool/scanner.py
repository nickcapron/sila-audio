"""
Sample pack scanner and importer.

Recursively finds audio files, groups them intelligently (skipping DAW wrapper
folders and BPM/key sub-folders), and copies them into ~/SILA/library/.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from pydantic import BaseModel

from sila.import_tool.mapper import suggest_category
from sila.security import safe_path, sanitize_library_filename, sanitize_project_name

AUDIO_EXTENSIONS = frozenset({".wav", ".aiff", ".aif"})

# Folder names that are DAW/format wrappers — look through them transparently.
_DAW_WRAPPERS = frozenset({
    "wav", "audio",
    "ableton", "kontakt", "battery",
    "logic", "maschine", "sfz",
    "reason", "mpc", "ni",
    "native instruments",
})

# BPM folders: "120BPM", "130 BPM", etc.
_BPM_RE = re.compile(r'^\d+\s*bpm$', re.IGNORECASE)

# Key folders: "Am", "C#", "Dbm", "F# Major", etc.
# Requires at least an accidental, a mode letter, or a written-out mode word.
_KEY_RE = re.compile(
    r'^[A-G][#b]$'                                       # C#, Db
    r'|^[A-G][#b]?\s*(?:major|minor|maj|min)$'          # D Major, C# minor
    r'|^[A-G][Mm]$'                                      # Am, CM
    r'|^[A-G][#b][Mm]$',                                 # C#m, Dbm
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------

class ScanGroupResult(BaseModel):
    name: str
    file_count: int
    suggestion: str | None  # canonical category or None


class ScanResult(BaseModel):
    groups: list[ScanGroupResult]
    total_files: int
    source_path: str


class ImportResult(BaseModel):
    imported: int
    skipped: int
    categories_created: int


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_bpm_or_key_folder(name: str) -> bool:
    return bool(_BPM_RE.match(name)) or bool(_KEY_RE.match(name))


def _get_group_name(rel_parts: tuple[str, ...], root_name: str) -> str:
    """Return the logical group name for a file given its path parts.

    Iterates from the outermost directory inward, skipping DAW wrappers and
    BPM/key sub-folders, and returns the first meaningful directory name.
    Falls back to *root_name* for flat files with no useful parent directory.
    """
    for part in rel_parts[:-1]:   # all components except the filename itself
        if part.lower() in _DAW_WRAPPERS:
            continue
        if _is_bpm_or_key_folder(part):
            continue
        return part
    return root_name


def _find_audio_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_folder(source_path: str) -> ScanResult:
    """Scan *source_path* recursively, group audio files, and suggest categories."""
    src = Path(source_path).resolve()
    if not src.is_dir():
        raise ValueError(f"Not a directory: {source_path!r}")

    root_name = src.name
    all_files = _find_audio_files(src)

    groups: dict[str, list[Path]] = {}
    for fpath in all_files:
        group = _get_group_name(fpath.relative_to(src).parts, root_name)
        groups.setdefault(group, []).append(fpath)

    result_groups = [
        ScanGroupResult(
            name=name,
            file_count=len(files),
            suggestion=suggest_category(name, [f.name for f in files]),
        )
        for name, files in sorted(groups.items())
    ]

    return ScanResult(
        groups=result_groups,
        total_files=len(all_files),
        source_path=str(src),
    )


def execute_import(
    source_path: str,
    pack_name: str,
    mappings: dict[str, str],
    library_root: Path,
) -> ImportResult:
    """Copy scanned files into *library_root*/<pack_name>/<category>/.

    *mappings* maps group name → canonical category name.  Groups absent from
    *mappings* are skipped.  Existing files are never overwritten.
    """
    src = Path(source_path).resolve()
    if not src.is_dir():
        raise ValueError(f"Source directory not found: {source_path!r}")

    sanitized_pack = sanitize_project_name(pack_name)
    if not sanitized_pack:
        raise ValueError("Pack name is empty after sanitization")

    pack_dir = safe_path(library_root, sanitized_pack)
    root_name = src.name
    all_files = _find_audio_files(src)

    imported = 0
    skipped = 0
    categories_created: set[str] = set()

    for fpath in all_files:
        group = _get_group_name(fpath.relative_to(src).parts, root_name)
        category = mappings.get(group)
        if category is None:
            skipped += 1
            continue

        cat_dir = safe_path(pack_dir, category)
        if not cat_dir.exists():
            cat_dir.mkdir(parents=True, exist_ok=True)
            categories_created.add(category)

        dest_name = sanitize_library_filename(fpath.name)
        dest = cat_dir / dest_name
        if dest.exists():
            skipped += 1
            continue

        shutil.copy2(fpath, dest)
        imported += 1

    return ImportResult(
        imported=imported,
        skipped=skipped,
        categories_created=len(categories_created),
    )
