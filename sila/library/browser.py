"""
Sample library browser.

Reads ~/SILA/library/ and returns a two-level pack/category/sample tree.
Ensures ~/SILA/library/My Samples/ exists with the canonical category
structure on first run.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import BaseModel

from sila.security import safe_path

LIBRARY_ROOT = Path.home() / "SILA" / "library"
MY_SAMPLES_NAME = "My Samples"
AUDIO_EXTENSIONS = frozenset({".wav", ".aiff", ".aif"})

CANONICAL_CATEGORIES = [
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
]


class SampleInfo(BaseModel):
    name: str        # filename without extension (display label)
    filename: str    # full filename including extension
    path: str        # relative path from LIBRARY_ROOT: pack/category/file.wav
    size_bytes: int


class CategoryInfo(BaseModel):
    name: str
    path: str        # relative path from LIBRARY_ROOT: pack/category
    samples: list[SampleInfo] = []


class PackInfo(BaseModel):
    name: str
    path: str        # pack folder name (direct child of LIBRARY_ROOT)
    categories: list[CategoryInfo] = []


def ensure_my_samples() -> None:
    """Create ~/SILA/library/My Samples/ with canonical categories. Idempotent."""
    my_samples = LIBRARY_ROOT / MY_SAMPLES_NAME
    my_samples.mkdir(parents=True, exist_ok=True)
    for cat in CANONICAL_CATEGORIES:
        (my_samples / cat).mkdir(exist_ok=True)


def get_library_tree() -> list[PackInfo]:
    """Return the full library tree: My Samples first, then other packs alphabetically."""
    if not LIBRARY_ROOT.exists():
        return []

    all_dirs = sorted(
        d for d in LIBRARY_ROOT.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )

    # Pin My Samples at the top regardless of alphabetical order.
    ordered: list[Path] = []
    my_samples_dir = LIBRARY_ROOT / MY_SAMPLES_NAME
    if my_samples_dir.exists():
        ordered.append(my_samples_dir)
    ordered.extend(d for d in all_dirs if d.name != MY_SAMPLES_NAME)

    packs: list[PackInfo] = []
    for pack_dir in ordered:
        pack = PackInfo(name=pack_dir.name, path=pack_dir.name)
        for cat_dir in sorted(
            d for d in pack_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ):
            samples: list[SampleInfo] = [
                SampleInfo(
                    name=f.stem,
                    filename=f.name,
                    path=f"{pack_dir.name}/{cat_dir.name}/{f.name}",
                    size_bytes=f.stat().st_size,
                )
                for f in sorted(cat_dir.iterdir())
                if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
            ]
            if samples:  # omit empty categories from the returned tree
                pack.categories.append(
                    CategoryInfo(
                        name=cat_dir.name,
                        path=f"{pack_dir.name}/{cat_dir.name}",
                        samples=samples,
                    )
                )
        packs.append(pack)
    return packs


def resolve_library_path(rel_path: str) -> Path:
    """Resolve a relative library path, blocking traversal attempts."""
    return safe_path(LIBRARY_ROOT, rel_path)


def copy_to_project(rel_path: str, samples_dir: Path) -> str:
    """
    Copy a library sample into a project's samples/ directory.
    Returns the filename. Does not overwrite if the file is already present.
    """
    src = resolve_library_path(rel_path)
    if not src.exists():
        raise FileNotFoundError(f"Library sample not found: {rel_path!r}")
    dest = samples_dir / src.name
    if not dest.exists():
        shutil.copy2(src, dest)
    return src.name
