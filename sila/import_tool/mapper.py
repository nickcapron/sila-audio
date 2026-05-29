"""
Keyword-based category suggester for the sample import tool.

Given a group name (folder) and optional filenames, suggests the most
appropriate SILA canonical category.  Returns None when no confident
match is found so the user must choose manually.
"""
from __future__ import annotations

import re


def _norm(text: str) -> str:
    """Lowercase and replace separators with spaces for consistent matching."""
    return text.lower().replace("-", " ").replace("_", " ").replace(".", " ")


def _has(text: str, *substrings: str) -> bool:
    return any(s in text for s in substrings)


def _word(text: str, *words: str) -> bool:
    """True if any of *words* appear as a whole word in *text*."""
    return any(bool(re.search(r'(?<![a-z])' + re.escape(w) + r'(?![a-z])', text))
               for w in words)


def _suggest_from_text(text: str) -> str | None:
    """Apply priority-ordered rules to a normalised text fragment."""
    t = _norm(text)

    # ── Drums ────────────────────────────────────────────────────────────────
    # Kick — must precede generic "bass"
    if _has(t, "kick", "bass drum", "bassdrum") or _word(t, "bd"):
        return "01. Kick"
    # Snare
    if _has(t, "snare") or _word(t, "sd"):
        return "02. Snare"
    # Clap
    if _has(t, "clap"):
        return "03. Clap"
    # Closed hat — specific before generic
    if _has(t, "closed hat", "hat closed", "chh") or _word(t, "ch"):
        return "04. Hi-Hat Closed"
    # Open hat — specific before generic
    if _has(t, "open hat", "hat open", "ohh") or _word(t, "oh"):
        return "05. Hi-Hat Open"
    # Generic hat → default to closed
    if _has(t, "hihat", "hi hat", "hat"):
        return "04. Hi-Hat Closed"

    if _has(t, "cymbal"):
        return "06. Cymbal"
    if _has(t, "ride"):
        return "07. Ride"
    if _has(t, "crash"):
        return "08. Crash"
    if _has(t, "tom"):
        return "09. Tom"
    if _has(t, "rim", "rimshot"):
        return "10. Rimshot"
    if _has(t, "sidestick"):
        return "11. Sidestick"
    if _has(t, "cowbell"):
        return "12. Cowbell"
    if _has(t, "conga"):
        return "13. Conga"
    if _has(t, "bongo"):
        return "14. Bongo"
    if _has(t, "tamb", "tambourine"):
        return "15. Tambourine"
    if _has(t, "shaker"):
        return "16. Shaker"
    if _has(t, "cabasa"):
        return "17. Cabasa"
    if _has(t, "maracas"):
        return "18. Maracas"
    if _has(t, "triangle"):
        return "19. Triangle"
    if _has(t, "perc"):
        return "20. Electronic Perc"

    # ── Synths / Instruments ─────────────────────────────────────────────────
    # Bass — after all kick/bassdrum checks so "bass" alone routes here
    if _has(t, "bass"):
        return "21. Bass - Sub"

    if _has(t, "lead"):
        return "25. Lead - Saw"
    if _has(t, "pad"):
        return "29. Pad - Warm"
    if _has(t, "piano"):
        return "33. Keys - Piano"
    if _has(t, "organ"):
        return "35. Keys - Organ"
    if _has(t, "keys", "keyboard"):
        return "33. Keys - Piano"
    if _word(t, "key"):
        return "33. Keys - Piano"
    if _has(t, "stab"):
        return "37. Stab"
    if _has(t, "brass", "horn"):
        return "38. Brass"
    if _has(t, "string", "violin", "cello", "viola"):
        return "39. Strings - Solo"
    if _has(t, "pluck"):
        return "41. Pluck - Guitar"
    if _has(t, "arp"):
        return "44. Arp"
    if _has(t, "drone"):
        return "45. Drone"

    # ── Vocals ───────────────────────────────────────────────────────────────
    if _has(t, "vocal", "vox", "voice", "choir", "sing"):
        return "48. Vocal - Chops"

    # ── FX ───────────────────────────────────────────────────────────────────
    if _has(t, "riser", "rise"):
        return "53. FX - Rise"
    if _has(t, "fall", "down"):
        return "54. FX - Fall"
    if _has(t, "impact", "hit"):
        return "55. FX - Impact"
    if _has(t, "noise"):
        return "56. FX - Noise"
    if _has(t, "glitch"):
        return "57. FX - Glitch"

    return None


def suggest_category(group_name: str, filenames: list[str] | None = None) -> str | None:
    """Return the best canonical category for *group_name*, or None if unknown.

    Falls back to scanning the first few *filenames* if the group name alone
    yields no match.
    """
    result = _suggest_from_text(group_name)
    if result:
        return result
    if filenames:
        for fn in filenames[:10]:
            result = _suggest_from_text(fn)
            if result:
                return result
    return None
