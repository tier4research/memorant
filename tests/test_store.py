"""Tests for OntologyStore methods."""

import pytest

from memorant import MemorantStore
from memorant_ontology.config import OntologyConfig
from memorant_ontology.store import OntologyStore


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test.db"
    core = MemorantStore(db_path)
    core.init()
    store = OntologyStore(db_path, OntologyConfig(enabled=True))
    store.init()
    return store


class TestMergeEntities:
    def test_merge_moves_relations_and_deletes_loser(self, store):
        """Merge transfers loser relations to survivor and removes loser."""
        core = MemorantStore(store.db_path)
        core.init()
        cid = core.add_claim("Test claim", source_pointer="test")

        with store.connect() as db:
            db.execute(
                "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type) "
                "VALUES (?, ?, ?, ?)",
                ("e1", "Tier4IT", "tier4it", "organization"),
            )
            db.execute(
                "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type) "
                "VALUES (?, ?, ?, ?)",
                ("e2", "Tier4ITx", "tier4itx", "organization"),
            )
            db.execute(
                "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type) "
                "VALUES (?, ?, ?, ?)",
                ("e3", "Miguel", "miguel", "person"),
            )
            db.execute(
                "INSERT INTO memorant_ontology_relations (id, source_entity_id, target_entity_id, relation, source_claim_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ("r1", "e1", "e3", "manages", cid),
            )
            db.execute(
                "INSERT INTO memorant_ontology_relations (id, source_entity_id, target_entity_id, relation, source_claim_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ("r2", "e2", "e3", "uses", cid),
            )
            db.commit()

        store.merge_entities("e1", "e2")

        with store.connect() as db:
            loser = db.execute(
                "SELECT * FROM memorant_ontology_entities WHERE id = ?", ("e2",)
            ).fetchone()
            assert loser is None
            moved = db.execute(
                "SELECT * FROM memorant_ontology_relations WHERE source_entity_id = ?",
                ("e1",),
            ).fetchall()
            assert len(moved) == 2

    def test_merge_with_duplicate_relations_no_crash(self, store):
        """Merging entities with equivalent relations does not violate UNIQUE."""
        core = MemorantStore(store.db_path)
        core.init()
        cid = core.add_claim("Test claim", source_pointer="test")

        with store.connect() as db:
            db.execute(
                "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type) "
                "VALUES (?, ?, ?, ?)",
                ("e1", "Alice", "alice", "person"),
            )
            db.execute(
                "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type) "
                "VALUES (?, ?, ?, ?)",
                ("e2", "Alicia", "alicia", "person"),
            )
            db.execute(
                "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type) "
                "VALUES (?, ?, ?, ?)",
                ("e3", "Paris", "paris", "place"),
            )
            db.execute(
                "INSERT INTO memorant_ontology_relations (id, source_entity_id, target_entity_id, relation, source_claim_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ("r1", "e1", "e3", "located_in", cid),
            )
            db.execute(
                "INSERT INTO memorant_ontology_relations (id, source_entity_id, target_entity_id, relation, source_claim_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ("r2", "e2", "e3", "located_in", cid),
            )
            db.commit()

        store.merge_entities("e1", "e2")

        with store.connect() as db:
            rels = db.execute(
                "SELECT * FROM memorant_ontology_relations WHERE source_entity_id = ?",
                ("e1",),
            ).fetchall()
            assert len(rels) == 1
            loser = db.execute(
                "SELECT * FROM memorant_ontology_entities WHERE id = ?", ("e2",)
            ).fetchone()
            assert loser is None

    def test_merge_nonexistent_entities_raises(self, store):
        with pytest.raises(ValueError):
            store.merge_entities("e1", "e2")

    def test_merge_ineligible_names_raises(self, store):
        core = MemorantStore(store.db_path)
        core.init()
        with store.connect() as db:
            db.execute(
                "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type) "
                "VALUES (?, ?, ?, ?)",
                ("e1", "Alice", "alice", "person"),
            )
            db.execute(
                "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type) "
                "VALUES (?, ?, ?, ?)",
                ("e2", "Bob", "bob", "person"),
            )
            db.commit()
        with pytest.raises(ValueError):
            store.merge_entities("e1", "e2")
