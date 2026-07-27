"""Tests for hygiene — FK-safe purge, escalation debounce, trust history survival."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from memorant import MemorantStore
from memorant_ontology.config import OntologyConfig
from memorant_ontology.store import OntologyStore
from memorant_ontology.hygiene import health, rollback, purge_invalid
from memorant_ontology.queue import check_escalation


class TestHealth:
    def test_health_report(self, tmp_path: Path):
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig(enabled=True))
        store.init()
        report = health(store)
        assert report["status"] == "ok"
        assert report["schema_initialized"] is True

    def test_health_uninitialized(self, tmp_path: Path):
        db_path = tmp_path / "empty.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig())
        # Don't call store.init() — tables don't exist
        report = health(store)
        assert report["status"] == "not_initialized"


class TestPurgeFKSafe:
    def test_purge_removes_invalid_entity_without_relations(self, tmp_path: Path):
        """F5: entity with no valid relations → purged."""
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig(enabled=True))
        store.init()

        with store.connect() as db:
            db.execute(
                "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type, is_valid, invalidated_at) "
                "VALUES ('e-dead', 'Dead', 'dead', 'person', 0, datetime('now', '-31 days'))"
            )
            db.execute(
                "INSERT INTO memorant_ontology_trust_history (entity_id, entity_name, old_tier, new_tier, reason) "
                "VALUES ('e-dead', 'Dead', 'untrusted', 'derived', 'test')"
            )
            db.commit()

        count = purge_invalid(store, older_than_days=30)
        assert count >= 1

        with store.connect() as db:
            dead = db.execute(
                "SELECT * FROM memorant_ontology_entities WHERE id = 'e-dead'"
            ).fetchone()
        assert dead is None

    def test_purge_preserves_entity_with_relations(self, tmp_path: Path):
        """F5: entity with surviving valid relation → NOT purged."""
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        cid = core.add_claim("Purge preserve test", source_pointer="test")
        store = OntologyStore(db_path, OntologyConfig(enabled=True))
        store.init()

        with store.connect() as db:
            db.execute(
                "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type, is_valid, invalidated_at) "
                "VALUES ('e1', 'Alive', 'alive', 'person', 0, datetime('now', '-31 days'))"
            )
            db.execute(
                "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type) "
                "VALUES ('e2', 'Target', 'target', 'concept')"
            )
            db.execute(
                "INSERT INTO memorant_ontology_relations (id, source_entity_id, target_entity_id, relation, source_claim_id) "
                "VALUES ('r1', 'e1', 'e2', 'uses', ?)", (cid,)
            )
            db.commit()

        purge_invalid(store, older_than_days=30)

        with store.connect() as db:
            alive = db.execute(
                "SELECT * FROM memorant_ontology_entities WHERE id = 'e1'"
            ).fetchone()
        # e1 has a valid relation → refused to delete
        assert alive is not None

    def test_purge_order_relations_first(self, tmp_path: Path):
        """F5: relations purged before entities."""
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        cid = core.add_claim("Purge order test", source_pointer="test")
        store = OntologyStore(db_path, OntologyConfig(enabled=True))
        store.init()

        with store.connect() as db:
            db.execute(
                "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type) "
                "VALUES ('e1', 'Alive', 'alive', 'person')"
            )
            db.execute(
                "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type, is_valid, invalidated_at) "
                "VALUES ('e2', 'Dead', 'dead', 'person', 0, datetime('now', '-31 days'))"
            )
            db.execute(
                "INSERT INTO memorant_ontology_relations "
                "(id, source_entity_id, target_entity_id, relation, source_claim_id, is_valid, invalidated_at) "
                "VALUES ('r-dead', 'e2', 'e1', 'uses', ?, 0, datetime('now', '-31 days'))", (cid,)
            )
            db.commit()

        count = purge_invalid(store, older_than_days=30)
        assert count >= 1

    def test_trust_history_survives_purge(self, tmp_path: Path):
        """W5: trust_history.entity_name is preserved after purge."""
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig(enabled=True))
        store.init()

        with store.connect() as db:
            db.execute(
                "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type, is_valid, invalidated_at) "
                "VALUES ('e-dead', 'Dead', 'dead', 'person', 0, datetime('now', '-31 days'))"
            )
            db.execute(
                "INSERT INTO memorant_ontology_trust_history (entity_id, entity_name, old_tier, new_tier, reason) "
                "VALUES ('e-dead', 'Dead', 'untrusted', 'derived', 'test')"
            )
            db.commit()

        purge_invalid(store, older_than_days=30)

        with store.connect() as db:
            history = db.execute(
                "SELECT * FROM memorant_ontology_trust_history WHERE entity_id = 'e-dead'"
            ).fetchone()
        assert history is not None
        assert history["entity_name"] == "Dead"  # Not NULL


class TestEscalationDebounce:
    def test_escalation_fires_on_threshold(self, tmp_path: Path):
        """F18: 100+ failed rows in 24h → escalation alert."""
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig(enabled=True, alert_failed_threshold=5))
        store.init()

        # Create claims BEFORE opening store connection to avoid database lock
        cids = []
        for i in range(10):
            cids.append(core.add_claim(f"escalation-test-{i}", source_pointer="test"))

        with store.connect() as db:
            for cid in cids:
                db.execute(
                    "INSERT INTO memorant_ontology_queue (claim_id, state, completed_at, last_error) "
                    "VALUES (?, 'failed', datetime('now'), 'test error')",
                    (cid,),
                )
            db.commit()

        config = OntologyConfig(enabled=True, alert_failed_threshold=5)
        assert check_escalation(store, config) is True

    def test_escalation_debounced(self, tmp_path: Path):
        """F18: second alert within 24h → no duplicate."""
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig(enabled=True, alert_failed_threshold=5))
        store.init()

        # Create claims BEFORE opening store connection to avoid database lock
        cids = []
        for i in range(10):
            cids.append(core.add_claim(f"debounce-test-{i}", source_pointer="test"))

        with store.connect() as db:
            for cid in cids:
                db.execute(
                    "INSERT INTO memorant_ontology_queue (claim_id, state, completed_at, last_error) "
                    "VALUES (?, 'failed', datetime('now'), 'test error')",
                    (cid,),
                )
            db.commit()

        config = OntologyConfig(enabled=True, alert_failed_threshold=5)
        # First alert fires
        assert check_escalation(store, config) is True
        # Second alert debounced
        assert check_escalation(store, config) is False


class TestRollback:
    def test_rollback_drops_tables(self, tmp_path: Path):
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig(enabled=True))
        store.init()

        rollback(store)
        with store.connect() as db:
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        assert "memorant_ontology_entities" not in tables
        assert "claim_units" in tables  # core table preserved
