"""SQLCipher helpers shared by Memorant suite components."""

from __future__ import annotations


def apply_key(db, key: str) -> None:
    """Apply a SQLCipher key without allowing quote-delimited SQL break-out.

    SQLCipher's PRAGMA key syntax is not consistently parameter-bindable across
    Python wrappers, so quote the string using SQLite's single-quote escaping.
    """
    escaped = key.replace("'", "''")
    db.execute(f"PRAGMA key = '{escaped}'")
