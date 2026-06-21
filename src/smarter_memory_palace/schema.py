from __future__ import annotations

SCHEMA = {
    "claim_units": """
        CREATE TABLE IF NOT EXISTS claim_units (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            content_hash TEXT UNIQUE,
            fact_refs TEXT DEFAULT '[]',
            source_type TEXT DEFAULT 'manual',
            source_pointer TEXT NOT NULL,
            first_encoded TEXT NOT NULL DEFAULT (datetime('now')),
            last_touched TEXT NOT NULL DEFAULT (datetime('now')),
            reinforcement_count INTEGER DEFAULT 0,
            emotional_markers TEXT DEFAULT '[]',
            is_valid INTEGER DEFAULT 1,
            valid_from TEXT,
            valid_until TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "claim_fts": """CREATE VIRTUAL TABLE IF NOT EXISTS claim_fts USING fts5(id UNINDEXED, content, tokenize='porter unicode61')""",
    "digest_history": """
        CREATE TABLE IF NOT EXISTS digest_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT NOT NULL,
            content TEXT NOT NULL,
            diff_from_prior TEXT,
            promoted INTEGER DEFAULT 0,
            promoted_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "resonance_log": """
        CREATE TABLE IF NOT EXISTS resonance_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            turn_context TEXT,
            claim_ids TEXT,
            fired INTEGER DEFAULT 0,
            used_by_agent INTEGER DEFAULT 0,
            latency_ms INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "standing_facts": """
        CREATE TABLE IF NOT EXISTS standing_facts (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            event_date TEXT,
            source_claim_ids TEXT DEFAULT '[]',
            category TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "arcs": """
        CREATE TABLE IF NOT EXISTS arcs (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','dormant','closed')),
            description TEXT,
            last_touched TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "arc_members": """
        CREATE TABLE IF NOT EXISTS arc_members (
            arc_id TEXT NOT NULL,
            claim_id TEXT NOT NULL,
            added_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (arc_id, claim_id)
        )
    """,
}
