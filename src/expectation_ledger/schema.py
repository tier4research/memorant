"""Expectation Ledger v1 schema — expectations, contracts, violations, runs."""

from __future__ import annotations

SCHEMA_V1 = {
    # ── Core expectation storage ──────────────────────────────────
    "expectations": """
        CREATE TABLE IF NOT EXISTS expectations (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            content_hash TEXT UNIQUE,
            source_type TEXT NOT NULL DEFAULT 'manual'
                CHECK(source_type IN ('manual', 'derived', 'external', 'contract')),
            source_pointer TEXT NOT NULL DEFAULT '',
            trust_tier TEXT NOT NULL DEFAULT 'untrusted'
                CHECK(trust_tier IN ('operator', 'verified', 'derived', 'untrusted')),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'violated', 'waived', 'superseded')),
            parent_contract_id TEXT,
            metadata TEXT DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (parent_contract_id) REFERENCES contracts(id)
        )
    """,

    "expectations_fts": """
        CREATE VIRTUAL TABLE IF NOT EXISTS expectations_fts
        USING fts5(id UNINDEXED, content, tokenize='porter unicode61')
    """,

    # ── Contracts (formal groupings of expectations) ─────────────
    "contracts": """
        CREATE TABLE IF NOT EXISTS contracts (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            version TEXT NOT NULL DEFAULT '1.0',
            source_pointer TEXT DEFAULT '',
            trust_tier TEXT NOT NULL DEFAULT 'untrusted'
                CHECK(trust_tier IN ('operator', 'verified', 'derived', 'untrusted')),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'deprecated', 'revoked')),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,

    # ── Violations ────────────────────────────────────────────────
    "violations": """
        CREATE TABLE IF NOT EXISTS violations (
            id TEXT PRIMARY KEY,
            expectation_id TEXT NOT NULL,
            run_id TEXT,
            severity TEXT NOT NULL DEFAULT 'warning'
                CHECK(severity IN ('info', 'warning', 'critical')),
            evidence TEXT DEFAULT '',
            recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (expectation_id) REFERENCES expectations(id),
            FOREIGN KEY (run_id) REFERENCES runs(id)
        )
    """,

    # ── Agent runs ────────────────────────────────────────────────
    "runs": """
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            agent_id TEXT DEFAULT '',
            session_id TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'started'
                CHECK(status IN ('started', 'completed', 'failed', 'aborted')),
            expectations_checked INTEGER DEFAULT 0,
            violations_found INTEGER DEFAULT 0,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            ended_at TEXT,
            metadata TEXT DEFAULT '{}'
        )
    """,

    # ── Run-expectation join (which expectations were checked) ────
    "run_expectations": """
        CREATE TABLE IF NOT EXISTS run_expectations (
            run_id TEXT NOT NULL,
            expectation_id TEXT NOT NULL,
            passed INTEGER NOT NULL DEFAULT 1,
            checked_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (run_id, expectation_id),
            FOREIGN KEY (run_id) REFERENCES runs(id),
            FOREIGN KEY (expectation_id) REFERENCES expectations(id)
        )
    """,

    # ── Migration tracking ────────────────────────────────────────
    "_steward_canary": """
        CREATE TABLE IF NOT EXISTS _steward_canary (
            version INTEGER PRIMARY KEY,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed INTEGER DEFAULT 0
        )
    """,
}


# Migration path (empty for v1 — no prior versions to migrate from)
MIGRATIONS: dict[int, str] = {}
