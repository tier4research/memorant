"""Context Tuner v1 schema — recovery store for message compression rollback."""

from __future__ import annotations

SCHEMA_V1 = {
    # ── Recovery sessions ──────────────────────────────────
    "recovery_sessions": """
        CREATE TABLE IF NOT EXISTS recovery_sessions (
            id TEXT PRIMARY KEY,
            original_messages TEXT NOT NULL,
            compressed_messages TEXT NOT NULL,
            original_tokens INTEGER NOT NULL DEFAULT 0,
            compressed_tokens INTEGER NOT NULL DEFAULT 0,
            compression_ratio REAL NOT NULL DEFAULT 0.0,
            session_metadata TEXT DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,

    # ── FTS5 index for searching compressed content ────────
    "recovery_sessions_fts": """
        CREATE VIRTUAL TABLE IF NOT EXISTS recovery_sessions_fts
        USING fts5(
            id UNINDEXED,
            searchable_content,
            tokenize='porter unicode61'
        )
    """,

    # ── Migration tracking ─────────────────────────────────
    "_steward_canary": """
        CREATE TABLE IF NOT EXISTS _steward_canary (
            version INTEGER PRIMARY KEY,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed INTEGER DEFAULT 0
        )
    """,
}

# Migration path (empty for v1 — no prior versions)
MIGRATIONS: dict[int, str] = {}
