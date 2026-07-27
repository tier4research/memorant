"""Name normalization, sanitization, and merge eligibility for ontology entities."""

from __future__ import annotations

import re
import unicodedata


def normalize_name(name: str) -> str:
    """Normalize a name for matching: lowercase, strip, collapse whitespace.

    Consistent with NOCASE collation in SQLite.
    """
    n = name.strip().lower()
    n = re.sub(r"\s+", " ", n)
    return n


def sanitize_name(name: str) -> str:
    """Sanitize a name for storage.

    Rules:
    - Replace newlines with spaces FIRST (before control char regex strips them)
    - Strip remaining control characters, bidi controls
    - Strip markup patterns
    - NFKC normalize
    - Collapse whitespace to single space
    - Cap at 80 chars; return empty if > 80 or empty after cleaning
    """
    # Replace newlines with space FIRST (before control char regex strips \x0a/\x0d)
    text = name.replace('\n', ' ').replace('\r', ' ')
    # Strip control characters (C0, C1) and bidi controls
    text = re.sub(r'[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u206f]', '', text)
    # Strip common markup patterns
    text = re.sub(r'<[^>]*>', '', text)
    text = re.sub(r'\[[^\]]*\]', '', text)
    # NFKC normalize
    text = unicodedata.normalize('NFKC', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    # Length check
    if len(text) > 80:
        return ""
    return text


def sanitize_description(text: str) -> str:
    """Sanitize a description for storage.

    Same rules as sanitize_name but with 120-char limit.
    """
    # Replace newlines with space FIRST
    text = text.replace('\n', ' ').replace('\r', ' ')
    # Strip control characters and bidi controls
    text = re.sub(r'[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u206f]', '', text)
    # Strip markup
    text = re.sub(r'<[^>]*>', '', text)
    text = re.sub(r'\[[^\]]*\]', '', text)
    # NFKC normalize
    text = unicodedata.normalize('NFKC', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    # Length check
    if len(text) > 120:
        return ""
    return text


def merge_eligible(name_a: str, name_b: str) -> bool:
    """Check if two entity names are eligible for merging.

    Criteria (all must hold):
    - Both names >= 5 characters after normalization
    - Levenshtein distance <= 2
    - Prefix match (first 4 chars identical after normalization)
    """
    a = normalize_name(name_a)
    b = normalize_name(name_b)

    if len(a) < 5 or len(b) < 5:
        return False

    dist = _levenshtein(a, b)
    if dist > 2:
        return False

    # Prefix check: first 4 chars must match
    if a[:4] != b[:4]:
        return False

    return True


def _levenshtein(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)

    if len(s2) == 0:
        return len(s1)

    prev_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row

    return prev_row[-1]
