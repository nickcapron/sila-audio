"""
Security primitives for SILA. Every module imports from here.
Enforces: 127.0.0.1-only binding, token auth on all routes, path traversal
prevention, notes sanitization, backup-before-write, and filename safety.
"""

import hmac
import os
import re
import secrets
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import Header, HTTPException, status

# The session token is persisted under ~/SILA and reused across restarts. This
# app binds to 127.0.0.1 only, so the token is a local-process guard rather
# than a network secret; persisting it means restarting the server no longer
# invalidates the token an already-open browser tab is holding (otherwise every
# request 401s and the UI looks broken — projects "vanish", saves fail).
_TOKEN_FILE = Path.home() / "SILA" / ".session_token"
_SESSION_TOKEN: str | None = None  # lazily loaded so import has no FS side effect


def _load_or_create_token() -> str:
    """Return the persisted token, or mint one and write it to ~/SILA.

    Falls back to an in-memory token (regenerated each start, the old behaviour)
    if the file can't be read or written for any reason.
    """
    try:
        if _TOKEN_FILE.is_file():
            existing = _TOKEN_FILE.read_text(encoding="utf-8").strip()
            if existing:
                return existing
    except OSError:
        pass

    token = secrets.token_urlsafe(32)
    try:
        _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(token, encoding="utf-8")
        try:
            os.chmod(_TOKEN_FILE, 0o600)  # best-effort: owner-only
        except OSError:
            pass
    except OSError:
        pass  # non-persistent fallback
    return token


def generate_session_token() -> str:
    """Return the session token, loading or creating it on first use."""
    global _SESSION_TOKEN
    if _SESSION_TOKEN is None:
        _SESSION_TOKEN = _load_or_create_token()
    return _SESSION_TOKEN


def verify_token(token: str) -> bool:
    """Constant-time comparison against the session token."""
    return hmac.compare_digest(token, generate_session_token())


def safe_path(base: str | Path, untrusted: str | Path) -> Path:
    """
    Resolve `untrusted` relative to `base` and verify it stays inside `base`.
    Raises ValueError on traversal attempts.
    """
    base = Path(base).resolve()
    candidate = (base / untrusted).resolve()
    if not candidate.is_relative_to(base):
        raise ValueError(f"Path traversal blocked: {untrusted!r} escapes {base}")
    return candidate


# Patterns that look like prompt injection in user-supplied text fields.
_INJECTION_PATTERNS = re.compile(
    r"(ignore\s+(previous|above|all)\s+instructions?"
    r"|system\s*prompt"
    r"|<\s*/?(?:system|assistant|user|instruction)\s*>"
    r"|```\s*(?:system|prompt)"
    r"|act\s+as\s+(?:a\s+)?(?:jailbreak|dan|unrestricted)"
    r"|\[\s*INST\s*\]"
    r"|<\|im_start\|>"
    r")",
    re.IGNORECASE,
)


def sanitize_notes(text: str) -> str:
    """
    Strip patterns that look like prompt injection from free-text notes.
    Returns the cleaned string; raises ValueError if the text is suspiciously
    large (>4 KB) — notes are human annotations, not documents.
    """
    if len(text) > 4096:
        raise ValueError("Notes field exceeds 4096 characters")
    return _INJECTION_PATTERNS.sub("[removed]", text)


def sanitize_filename(name: str) -> str:
    """
    Return an ASCII-safe filename: spaces → underscores, non-ASCII stripped,
    truncated to 16 chars, no leading/trailing dots or underscores.
    """
    # Replace spaces first, then strip non-ASCII/non-safe chars.
    name = name.replace(" ", "_")
    name = re.sub(r"[^A-Za-z0-9_\-.]", "", name)
    name = name.strip("._")
    return name[:16] if name else "untitled"


_WIN_RESERVED = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?$",
    re.IGNORECASE,
)


def sanitize_project_name(name: str) -> str:
    """
    Return a filesystem-safe project directory name.

    Same rules as sanitize_filename but with a 64-character limit instead of
    16, since project names are not constrained by Digitakt hardware limits.
    Spaces become underscores; non-ASCII and OS-reserved characters are stripped.
    Returns "" if the result is empty or matches a Windows reserved device name
    (CON, NUL, COM1-COM9, LPT1-LPT9 with or without extension).
    """
    name = name.replace(" ", "_")
    name = re.sub(r"[^A-Za-z0-9_\-.]", "", name)
    name = name.strip("._")
    name = name[:64]
    if _WIN_RESERVED.match(name):
        return ""
    return name


def sanitize_library_filename(name: str) -> str:
    """Return a filesystem-safe filename for library copies.

    Same character rules as sanitize_filename but with a 64-char limit on the
    stem instead of 16, and preserves the original file extension.
    """
    p = Path(name)
    ext = p.suffix.lower()
    stem = p.stem.replace(" ", "_")
    stem = re.sub(r"[^A-Za-z0-9_\-.]", "", stem)
    stem = stem.strip("._")
    stem = stem[:64] if stem else "sample"
    return stem + ext


def backup_before_write(path: str | Path) -> Path:
    """
    Copy `path` to a timestamped backup in the same directory and return the
    backup path. No-ops (returns the same path) if the file does not yet exist.
    """
    path = Path(path)
    if not path.exists():
        return path
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = path.with_suffix(f".{ts}.bak{path.suffix}")
    shutil.copy2(path, backup)
    return backup


async def require_token(x_sila_token: str = Header(...)) -> None:
    """FastAPI Depends() — validates the session token on every request."""
    if not verify_token(x_sila_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing SILA session token",
        )
