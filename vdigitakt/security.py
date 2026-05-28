"""
Security primitives for VDigitakt. Every module imports from here.
Enforces: 127.0.0.1-only binding, token auth on all routes, path traversal
prevention, notes sanitization, backup-before-write, and filename safety.
"""

import hmac
import re
import secrets
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import Header, HTTPException, status

# Generated once at process startup — never logged, never persisted.
_SESSION_TOKEN: str = secrets.token_urlsafe(32)


def generate_session_token() -> str:
    """Return the session token for this process run."""
    return _SESSION_TOKEN


def verify_token(token: str) -> bool:
    """Constant-time comparison against the session token."""
    return hmac.compare_digest(token, _SESSION_TOKEN)


def safe_path(base: str | Path, untrusted: str | Path) -> Path:
    """
    Resolve `untrusted` relative to `base` and verify it stays inside `base`.
    Raises ValueError on traversal attempts.
    """
    base = Path(base).resolve()
    candidate = (base / untrusted).resolve()
    if not str(candidate).startswith(str(base)):
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


async def require_token(x_vdigitakt_token: str = Header(...)) -> None:
    """FastAPI Depends() — validates the session token on every request."""
    if not verify_token(x_vdigitakt_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing VDigitakt session token",
        )
