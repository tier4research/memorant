"""Memorant Auto-Ontology schema — tables, indexes, and migrations.

SCHEMA_ONTOLOGY contains all SQL for ontology-specific tables.
run_migration() is idempotent and NEVER touches core's PRAGMA user_version.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import OntologyStore


def _split_statements(sql: str) -> list[str]:
    """Split a multi-statement SQL block into individual statements.

    sqlite3.Connection.execute() only supports one statement at a time.
    """
    stmts = []
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        # Skip empty and comment-only statements
        if stmt and not stmt.startswith("--"):
            stmts.append(stmt)
    return stmts


SCHEMA_ONTOLOGY = {
    # claim_units extension — handled separately in run_migration (ALTER TABLE)
    "idx_claim_unprocessed": """
        CREATE INDEX IF NOT EXISTS idx_claim_ontology_unprocessed
            ON claim_units(id) WHERE ontology_processed_at IS NULL
    """,
    "entities": """
        CREATE TABLE IF NOT EXISTS memorant_ontology_entities (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            name TEXT NOT NULL CHECK(length(name) <= 80),
            name_normalized TEXT NOT NULL COLLATE NOCASE,
            entity_type TEXT NOT NULL,
            description TEXT CHECK(description IS NULL OR length(description) <= 120),
            attributes TEXT DEFAULT '{}' CHECK(json_valid(attributes) AND length(attributes) <= 2000),
            first_seen_claim_id TEXT,
            last_seen_claim_id TEXT,
            last_seen_at TEXT,
            reinforcement_count INTEGER DEFAULT 1,
            trust_tier TEXT NOT NULL DEFAULT 'derived'
                CHECK(trust_tier IN ('operator', 'verified', 'derived', 'untrusted')),
            is_valid INTEGER DEFAULT 1 CHECK(is_valid IN (0, 1)),
            invalidated_at TEXT,
            prompt_version TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(tenant_id, name_normalized, entity_type),
            FOREIGN KEY (first_seen_claim_id) REFERENCES claim_units(id) ON DELETE SET NULL,
            FOREIGN KEY (last_seen_claim_id) REFERENCES claim_units(id) ON DELETE SET NULL
        )
    """,
    "relations": """
        CREATE TABLE IF NOT EXISTS memorant_ontology_relations (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            source_entity_id TEXT NOT NULL,
            target_entity_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            source_claim_id TEXT NOT NULL,
            claim_role TEXT NOT NULL DEFAULT 'asserts'
                CHECK(claim_role IN ('asserts', 'mentions', 'contradicts')),
            trust_tier TEXT NOT NULL DEFAULT 'derived'
                CHECK(trust_tier IN ('operator', 'verified', 'derived', 'untrusted')),
            confidence REAL DEFAULT 0.5 CHECK(0.0 <= confidence AND confidence <= 1.0),
            prompt_version TEXT,
            is_valid INTEGER DEFAULT 1 CHECK(is_valid IN (0, 1)),
            invalidated_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(source_entity_id, target_entity_id, relation, source_claim_id),
            FOREIGN KEY (source_entity_id) REFERENCES memorant_ontology_entities(id) ON DELETE CASCADE,
            FOREIGN KEY (target_entity_id) REFERENCES memorant_ontology_entities(id) ON DELETE CASCADE,
            FOREIGN KEY (source_claim_id) REFERENCES claim_units(id) ON DELETE CASCADE
        )
    """,
    "idx_relations_source_target": """
        CREATE INDEX IF NOT EXISTS idx_ontology_relations_source_target
            ON memorant_ontology_relations(source_entity_id, target_entity_id, is_valid, relation)
    """,
    "idx_relations_target": """
        CREATE INDEX IF NOT EXISTS idx_ontology_relations_target
            ON memorant_ontology_relations(target_entity_id, is_valid)
    """,
    "idx_relations_claim": """
        CREATE INDEX IF NOT EXISTS idx_ontology_relations_claim
            ON memorant_ontology_relations(source_claim_id)
    """,
    "queue": """
        CREATE TABLE IF NOT EXISTS memorant_ontology_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            enqueued_at TEXT NOT NULL DEFAULT (datetime('now')),
            started_at TEXT,
            completed_at TEXT,
            worker_id TEXT,
            attempts INTEGER DEFAULT 0,
            last_error TEXT,
            state TEXT NOT NULL DEFAULT 'pending'
                CHECK(state IN ('pending', 'in_progress', 'complete', 'failed')),
            cost_usd REAL DEFAULT 0,
            prompt_version TEXT,
            FOREIGN KEY (claim_id) REFERENCES claim_units(id) ON DELETE CASCADE
        )
    """,
    "idx_queue_state": """
        CREATE INDEX IF NOT EXISTS idx_ontology_queue_state
            ON memorant_ontology_queue(state, enqueued_at)
    """,
    "ux_queue_active": """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_ontology_queue_active
            ON memorant_ontology_queue(claim_id) WHERE state IN ('pending', 'in_progress')
    """,
    "trust_history": """
        CREATE TABLE IF NOT EXISTS memorant_ontology_trust_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL,
            entity_name TEXT NOT NULL,
            old_tier TEXT NOT NULL,
            new_tier TEXT NOT NULL,
            reason TEXT NOT NULL,
            claim_id TEXT,
            at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "review": """
        CREATE TABLE IF NOT EXISTS memorant_ontology_review (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            relation_id_1 TEXT NOT NULL,
            relation_id_2 TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'reviewed', 'dismissed')),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            reviewed_at TEXT
        )
    """,
    "meta": """
        CREATE TABLE IF NOT EXISTS memorant_ontology_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
}


def run_migration(store: "OntologyStore") -> None:
    """Idempotent. Run on every init(). Never touches core's PRAGMA user_version."""
    with store.connect() as db:
        # Guard: skip schema migration if core table doesn't exist
        core_exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='claim_units'").fetchone()
        if not core_exists:
            print(
                "Warning: Cannot initialize ontology schema — memorant core table 'claim_units' not found. "
                "Initialize memorant first, then re-run `memorant-ontology init`."
            )
            return

        # 1. Add claim_units extension columns if missing (F10 — no IF NOT EXISTS for ALTER)
        existing = {row["name"] for row in db.execute("PRAGMA table_info(claim_units)").fetchall()}
        for col, decl in [
            ("ontology_processed_at", "TEXT"),
            ("ontology_prompt_version", "TEXT"),
            ("ontology_cost_usd", "REAL DEFAULT 0"),
        ]:
            if col not in existing:
                db.execute(f"ALTER TABLE claim_units ADD COLUMN {col} {decl}")

        # 2. Create all other tables/indexes (IF NOT EXISTS handles re-run)
        for key, sql in SCHEMA_ONTOLOGY.items():
            for stmt in _split_statements(sql):
                db.execute(stmt)

        # 3. Seed meta with INSERT OR IGNORE (F7 — do not clobber last_purge_at on rerun)
        db.execute("INSERT OR IGNORE INTO memorant_ontology_meta (key, value) VALUES ('schema_version', '1')")
        db.execute("INSERT OR IGNORE INTO memorant_ontology_meta (key, value) VALUES ('last_purge_at', '')")
        db.execute("INSERT OR IGNORE INTO memorant_ontology_meta (key, value) VALUES ('last_alert_at', '')")
        db.commit()
