"""Tests for ontology schema — migration, idempotency, rollback, PRAGMAs."""

import sqlite3
from pathlib import Path

import pytest

from memorant import MemorantStore
from memorant_ontology.config import OntologyConfig
from memorant_ontology.store import OntologyStore
from memorant_ontology.schema import run_migration, SCHEMA_ONTOLOGY


class TestSchemaCreation:
    """Fresh DB → all tables created."""

    def test_fresh_db_creates_all_tables(self, tmp_path: Path):
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig())
        store.init()

        with store.connect() as db:
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}

        for expected in [
            "memorant_ontology_entities",
            "memorant_ontology_relations",
            "memorant_ontology_queue",
            "memorant_ontology_trust_history",
            "memorant_ontology_review",
            "memorant_ontology_meta",
        ]:
            assert expected in tables, f"Missing table: {expected}"

    def test_pragma_user_version_unchanged(self, tmp_path: Path):
        """M1: plugin must NEVER write PRAGMA user_version."""
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        version_before = core._steward.user_version

        store = OntologyStore(db_path, OntologyConfig())
        store.init()

        version_after = core._steward.user_version
        assert version_before == version_after, "Ontology init must not change user_version"

    def test_claim_units_extension_columns(self, tmp_path: Path):
        """claim_units has 3 new ontology columns after init."""
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig())
        store.init()

        with store.connect() as db:
            columns = {row[1] for row in db.execute(
                "PRAGMA table_info(claim_units)"
            ).fetchall()}

        assert "ontology_processed_at" in columns
        assert "ontology_prompt_version" in columns
        assert "ontology_cost_usd" in columns

    def test_indexes_created(self, tmp_path: Path):
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig())
        store.init()

        with store.connect() as db:
            indexes = {row[1] for row in db.execute(
                "SELECT * FROM sqlite_master WHERE type='index'"
            ).fetchall()}

        assert "idx_claim_ontology_unprocessed" in indexes
        assert "idx_ontology_relations_source_target" in indexes
        assert "idx_ontology_queue_state" in indexes


class TestSchemaIdempotency:
    """Re-run init() → no error; meta values preserved."""

    def test_rerun_init_no_error(self, tmp_path: Path):
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig())
        store.init()
        # Second run should not raise
        store.init()

    def test_meta_last_purge_at_not_clobbered(self, tmp_path: Path):
        """F7: INSERT OR IGNORE preserves existing meta values on rerun."""
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig())
        store.init()

        # Set a value
        with store.connect() as db:
            db.execute(
                "UPDATE memorant_ontology_meta SET value = '2026-01-01' WHERE key = 'last_purge_at'"
            )
            db.commit()

        # Re-init
        store.init()

        with store.connect() as db:
            row = db.execute(
                "SELECT value FROM memorant_ontology_meta WHERE key = 'last_purge_at'"
            ).fetchone()
        assert row["value"] == "2026-01-01"


class TestSchemaRollback:
    """Rollback drops ontology tables; claim_units extension columns remain."""

    def test_rollback_drops_ontology_tables(self, tmp_path: Path):
        from memorant_ontology.hygiene import rollback

        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig())
        store.init()

        rollback(store)

        with store.connect() as db:
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}

        for dropped in [
            "memorant_ontology_entities",
            "memorant_ontology_relations",
            "memorant_ontology_queue",
            "memorant_ontology_meta",
        ]:
            assert dropped not in tables

        # claim_units still exists
        assert "claim_units" in tables

    def test_rollback_preserves_extension_columns(self, tmp_path: Path):
        from memorant_ontology.hygiene import rollback

        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig())
        store.init()
        rollback(store)

        with store.connect() as db:
            columns = {row[1] for row in db.execute(
                "PRAGMA table_info(claim_units)"
            ).fetchall()}

        # Extension columns remain after rollback
        assert "ontology_processed_at" in columns
        assert "ontology_prompt_version" in columns
        assert "ontology_cost_usd" in columns


class TestPragmas:
    """PRAGMA enforcement tests."""

    def test_foreign_keys_on(self, tmp_path: Path):
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig())
        store.init()

        with store.connect() as db:
            fk = db.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1

    def test_journal_mode_wal(self, tmp_path: Path):
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig())
        store.init()

        with store.connect() as db:
            mode = db.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

    def test_busy_timeout_matches_steward(self, tmp_path: Path):
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig())
        store.init()

        with store.connect() as db:
            timeout = db.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == 5000  # steward default


class TestFKEnforcement:
    """FK enforcement: bad references raise IntegrityError."""

    def test_relation_with_nonexistent_entity_fails(self, tmp_path: Path):
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig())
        store.init()

        with store.connect() as db:
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO memorant_ontology_relations "
                    "(id, source_entity_id, target_entity_id, relation, source_claim_id) "
                    "VALUES ('r1', 'nonexistent', 'also-nonexistent', 'uses', 'fake-claim')"
                )

    def test_claim_role_check_constraint(self, tmp_path: Path):
        """m20: claim_role CHECK rejects invalid values."""
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        # Add a claim for FK
        cid = core.add_claim("test claim", source_pointer="test")
        store = OntologyStore(db_path, OntologyConfig())
        store.init()

        with store.connect() as db:
            # Create valid entities first
            db.execute(
                "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type) "
                "VALUES ('e1', 'Test', 'test', 'concept')"
            )
            db.execute(
                "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type) "
                "VALUES ('e2', 'Other', 'other', 'concept')"
            )
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO memorant_ontology_relations "
                    "(id, source_entity_id, target_entity_id, relation, source_claim_id, claim_role) "
                    "VALUES ('r1', 'e1', 'e2', 'uses', ?, 'speculative')",
                    (cid,),
                )
