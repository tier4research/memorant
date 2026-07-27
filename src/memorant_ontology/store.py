"""OntologyStore — SQLite connection manager for ontology tables.

Reuses PRAGMA pattern from core.py (WAL, foreign_keys, busy_timeout via Steward).
NEVER touches core's PRAGMA user_version (M1, F10).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from memorant._vendor.steward import Steward

if TYPE_CHECKING:
    from .config import OntologyConfig


class OntologyStore:
    """Manages SQLite connections for ontology tables.

    Reuses core's PRAGMA pattern (WAL, foreign_keys, busy_timeout).
    Plugin schema version lives in memorant_ontology_meta.schema_version only.
    """

    def __init__(self, db_path: str | Path, config: "OntologyConfig | None" = None):
        from .config import OntologyConfig

        self.db_path = Path(db_path)
        self.config = config or OntologyConfig()
        # Read core's busy_timeout so we match. Steward defaults to 5000ms.
        self._steward = Steward(self.db_path)
        self._busy_timeout_ms = self._steward.busy_timeout_ms

    def connect(self) -> sqlite3.Connection:
        """Open a connection with WAL, foreign_keys, and busy_timeout."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.db_path))
        db.row_factory = sqlite3.Row
        # Match core's PRAGMA pattern at core.py:119-121
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        return db

    def init(self) -> None:
        """Initialize ontology schema. Idempotent. NEVER touches core's user_version."""
        from .schema import run_migration

        run_migration(self)

    def invalidate_entity(self, entity_id: str) -> int:
        """Mark an entity as invalid. Returns rowcount."""
        with self.connect() as db:
            cur = db.execute(
                "UPDATE memorant_ontology_entities SET is_valid = 0, "
                "invalidated_at = datetime('now'), updated_at = datetime('now') "
                "WHERE id = ? AND is_valid = 1",
                (entity_id,),
            )
            db.commit()
        return cur.rowcount

    def invalidate_entity_from_claim(self, claim_id: str) -> int:
        """Invalidate entities whose first_seen or last_seen is the given claim."""
        with self.connect() as db:
            cur = db.execute(
                "UPDATE memorant_ontology_entities SET is_valid = 0, "
                "invalidated_at = datetime('now'), updated_at = datetime('now') "
                "WHERE (first_seen_claim_id = ? OR last_seen_claim_id = ?) AND is_valid = 1",
                (claim_id, claim_id),
            )
            db.commit()
        return cur.rowcount

    def merge_entities(self, survivor_id: str, loser_id: str) -> None:
        """Merge loser entity into survivor.

        Transfers all relations, increments reinforcement_count, deletes loser.
        History row written for the merge.
        """
        if survivor_id == loser_id:
            raise ValueError(f"Cannot merge entity '{survivor_id}' with itself")
        from .normalizer import merge_eligible

        with self.connect() as db:
            # Fetch both entities
            survivor = db.execute(
                "SELECT * FROM memorant_ontology_entities WHERE id = ?", (survivor_id,)
            ).fetchone()
            loser = db.execute(
                "SELECT * FROM memorant_ontology_entities WHERE id = ?", (loser_id,)
            ).fetchone()
            if not survivor or not loser:
                raise ValueError("Both entities must exist")

            # Gate: merge_eligible check
            if not merge_eligible(survivor["name"], loser["name"]):
                raise ValueError(
                    f"Entities not eligible for merge: '{survivor['name']}' vs '{loser['name']}'"
                )

            # Transfer relations from loser to survivor. UPDATE OR IGNORE avoids
            # UNIQUE constraint violations when an equivalent survivor relation
            # already exists; leftover loser rows are then deleted.
            db.execute(
                "UPDATE OR IGNORE memorant_ontology_relations SET source_entity_id = ? "
                "WHERE source_entity_id = ?",
                (survivor_id, loser_id),
            )
            db.execute(
                "DELETE FROM memorant_ontology_relations WHERE source_entity_id = ?",
                (loser_id,),
            )
            db.execute(
                "UPDATE OR IGNORE memorant_ontology_relations SET target_entity_id = ? "
                "WHERE target_entity_id = ?",
                (survivor_id, loser_id),
            )
            db.execute(
                "DELETE FROM memorant_ontology_relations WHERE target_entity_id = ?",
                (loser_id,),
            )

            # Increment survivor reinforcement
            db.execute(
                "UPDATE memorant_ontology_entities SET "
                "reinforcement_count = reinforcement_count + ?, "
                "updated_at = datetime('now') WHERE id = ?",
                (loser["reinforcement_count"] or 1, survivor_id),
            )

            # Record trust history
            db.execute(
                "INSERT INTO memorant_ontology_trust_history "
                "(entity_id, entity_name, old_tier, new_tier, reason) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    loser_id,
                    loser["name"],
                    loser["trust_tier"],
                    survivor["trust_tier"],
                    f"merged into {survivor_id}",
                ),
            )

            # Delete loser (FK CASCADE handles orphaned relations)
            db.execute("DELETE FROM memorant_ontology_entities WHERE id = ?", (loser_id,))
            db.commit()
