"""Utilities for normalizing terminal/build error output."""

from __future__ import annotations

import re
from typing import Iterable, List


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_ERROR_LOCATION_RE = re.compile(r"(:\d+(?::\d+)?|\[\d+,\d+\])")


def strip_ansi(text: str) -> str:
    """Remove ANSI terminal escape sequences from text."""
    if not text:
        return ""
    return _ANSI_ESCAPE_RE.sub("", text)


def normalize_error_lines(lines: Iterable[str]) -> List[str]:
    """Return non-empty, ANSI-free diagnostic lines."""
    cleaned: List[str] = []
    for line in lines:
        plain = strip_ansi(line)
        if plain and plain.strip():
            cleaned.append(plain.strip())
    return cleaned


def normalize_error_identity(error_line: str) -> str:
    """Normalize one diagnostic line for location-insensitive comparison."""
    plain = strip_ansi(error_line)
    return _ERROR_LOCATION_RE.sub("", plain).strip()
