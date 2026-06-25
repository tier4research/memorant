"""Memorant v1 schema — claim units, relations, trust, digests, resonance."""

from __future__ import annotations

SCHEMA_V1 = {
    # ── Core claim storage ──────────────────────────────────
    "claim_units": """
        CREATE TABLE IF NOT EXISTS claim_units (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            content_hash TEXT UNIQUE,
            fact_refs TEXT DEFAULT '[]',
            source_type TEXT DEFAULT 'manual',
            source_pointer TEXT NOT NULL,
            trust_tier TEXT NOT NULL DEFAULT 'untrusted'
                CHECK(trust_tier IN ('operator', 'verified', 'derived', 'untrusted')),
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

    "claim_fts": """
        CREATE VIRTUAL TABLE IF NOT EXISTS claim_fts
        USING fts5(id UNINDEXED, content, tokenize='porter unicode61')
    """,

    # ── Claim relations (v1) ───────────────────────────────
    "supersedes": """
        CREATE TABLE IF NOT EXISTS supersedes (
            superseding_id TEXT NOT NULL,
            superseded_id TEXT NOT NULL,
            reason TEXT,
            recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (superseding_id, superseded_id),
            FOREIGN KEY (superseding_id) REFERENCES claim_units(id),
            FOREIGN KEY (superseded_id) REFERENCES claim_units(id)
        )
    """,

    "corrects": """
        CREATE TABLE IF NOT EXISTS corrects (
            correcting_id TEXT NOT NULL,
            corrected_id TEXT NOT NULL,
            reason TEXT,
            recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (correcting_id, corrected_id),
            FOREIGN KEY (correcting_id) REFERENCES claim_units(id),
            FOREIGN KEY (corrected_id) REFERENCES claim_units(id)
        )
    """,

    "derived_from": """
        CREATE TABLE IF NOT EXISTS derived_from (
            derived_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (derived_id, source_id),
            FOREIGN KEY (derived_id) REFERENCES claim_units(id),
            FOREIGN KEY (source_id) REFERENCES claim_units(id)
        )
    """,

    # ── Digest history (v1: TEXT state) ────────────────────
    "digest_history": """
        CREATE TABLE IF NOT EXISTS digest_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT NOT NULL,
            content TEXT NOT NULL,
            diff_from_prior TEXT,
            state TEXT NOT NULL DEFAULT 'pending'
                CHECK(state IN ('pending', 'promoted', 'rejected')),
            promoted_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,

    # ── Resonance log ──────────────────────────────────────
    "resonance_log": """
        CREATE TABLE IF NOT EXISTS resonance_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            turn_context TEXT,
            claim_ids TEXT,
            fired INTEGER DEFAULT 0,
            used_by_agent INTEGER DEFAULT 0,
            latency_ms INTEGER,
            retention_mode TEXT DEFAULT 'full'
                CHECK(retention_mode IN ('full', 'minimal', 'none')),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,

    # ── Standing facts (preserved from alpha) ──────────────
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

    # ── Arcs (preserved from alpha) ────────────────────────
    "arcs": """
        CREATE TABLE IF NOT EXISTS arcs (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL DEFAULT 'active'
                CHECK(state IN ('active', 'dormant', 'closed')),
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

    # ── Migration tracking (v1 steward canary) ─────────────
    "_steward_canary": """
        CREATE TABLE IF NOT EXISTS _steward_canary (
            version INTEGER PRIMARY KEY,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed INTEGER DEFAULT 0
        )
    """,
}


# Migration from v0.1 alpha: add trust_tier column to claim_units,
# convert digest_history.promoted to state, add relation tables
MIGRATIONS = {
    1: """
        ALTER TABLE claim_units ADD COLUMN trust_tier TEXT NOT NULL DEFAULT 'untrusted'
            CHECK(trust_tier IN ('operator', 'verified', 'derived', 'untrusted'));
    """,
    2: """
        CREATE TABLE IF NOT EXISTS supersedes (
            superseding_id TEXT NOT NULL,
            superseded_id TEXT NOT NULL,
            reason TEXT,
            recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (superseding_id, superseded_id),
            FOREIGN KEY (superseding_id) REFERENCES claim_units(id),
            FOREIGN KEY (superseded_id) REFERENCES claim_units(id)
        );
    """,
    3: """
        CREATE TABLE IF NOT EXISTS corrects (
            correcting_id TEXT NOT NULL,
            corrected_id TEXT NOT NULL,
            reason TEXT,
            recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (correcting_id, corrected_id),
            FOREIGN KEY (correcting_id) REFERENCES claim_units(id),
            FOREIGN KEY (corrected_id) REFERENCES claim_units(id)
        );
    """,
    4: """
        CREATE TABLE IF NOT EXISTS derived_from (
            derived_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (derived_id, source_id),
            FOREIGN KEY (derived_id) REFERENCES claim_units(id),
            FOREIGN KEY (source_id) REFERENCES claim_units(id)
        );
    """,
    5: """\
        CREATE TABLE IF NOT EXISTS digest_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT NOT NULL,
            content TEXT NOT NULL,
            diff_from_prior TEXT,
            promoted INTEGER DEFAULT 0,
            promoted_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        ALTER TABLE digest_history ADD COLUMN state TEXT NOT NULL DEFAULT 'pending'
            CHECK(state IN ('pending', 'promoted', 'rejected'));
    """,
    6: """
        UPDATE digest_history SET state = CASE
            WHEN promoted = 0 THEN 'pending'
            WHEN promoted = 1 THEN 'promoted'
            WHEN promoted = 2 THEN 'rejected'
            ELSE 'pending'
        END;
    """,
    7: """\
        CREATE TABLE IF NOT EXISTS resonance_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            turn_context TEXT,
            claim_ids TEXT,
            fired INTEGER DEFAULT 0,
            used_by_agent INTEGER DEFAULT 0,
            latency_ms INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        ALTER TABLE resonance_log ADD COLUMN retention_mode TEXT DEFAULT 'full'
            CHECK(retention_mode IN ('full', 'minimal', 'none'));
    """,
}
