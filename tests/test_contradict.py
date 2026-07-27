"""Tests for contradict — functional vs multi-valued relation contradictions."""

from pathlib import Path

import pytest

from memorant import MemorantStore
from memorant_ontology.config import OntologyConfig
from memorant_ontology.store import OntologyStore
from memorant_ontology.contradict import check_contradictions, check_contradictions_for_relation


@pytest.fixture
def contradiction_store(tmp_path: Path):
    """Store with entities and some relations for contradiction testing."""
    db_path = tmp_path / "test.db"
    core = MemorantStore(db_path)
    core.init()
    cid = core.add_claim("Miguel lives in Paris and uses Python and uses Go", source_pointer="test")

    store = OntologyStore(db_path, OntologyConfig(enabled=True))
    store.init()

    with store.connect() as db:
        db.execute(
            "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type, first_seen_claim_id, last_seen_claim_id) "
            "VALUES ('e-miguel', 'Miguel', 'miguel', 'person', ?, ?)", (cid, cid)
        )
        db.execute(
            "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type, first_seen_claim_id, last_seen_claim_id) "
            "VALUES ('e-paris', 'Paris', 'paris', 'place', ?, ?)", (cid, cid)
        )
        db.execute(
            "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type, first_seen_claim_id, last_seen_claim_id) "
            "VALUES ('e-london', 'London', 'london', 'place', ?, ?)", (cid, cid)
        )
        db.execute(
            "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type, first_seen_claim_id, last_seen_claim_id) "
            "VALUES ('e-python', 'Python', 'python', 'tool', ?, ?)", (cid, cid)
        )
        db.execute(
            "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type, first_seen_claim_id, last_seen_claim_id) "
            "VALUES ('e-go', 'Go', 'go', 'tool', ?, ?)", (cid, cid)
        )
        # Existing relation: Miguel lives_in Paris
        db.execute(
            "INSERT INTO memorant_ontology_relations (id, source_entity_id, target_entity_id, relation, source_claim_id) "
            "VALUES ('r-lives-paris', 'e-miguel', 'e-paris', 'located_in', ?)", (cid,)
        )
        # Existing relation: Miguel uses Python
        db.execute(
            "INSERT INTO memorant_ontology_relations (id, source_entity_id, target_entity_id, relation, source_claim_id) "
            "VALUES ('r-uses-python', 'e-miguel', 'e-python', 'uses', ?)", (cid,)
        )
        db.commit()

    return store


class TestFunctionalContradiction:
    def test_functional_relation_triggers_review(self, contradiction_store):
        """F2: Miguel lives_in Paris + Miguel lives_in London → review."""
        config = OntologyConfig(enabled=True)
        result = check_contradictions(
            contradiction_store,
            source_entity_id="e-miguel",
            relation="located_in",
            target_entity_id="e-london",
            config=config,
        )
        assert result is True

        # Verify review row created
        with contradiction_store.connect() as db:
            reviews = db.execute(
                "SELECT * FROM memorant_ontology_review WHERE status = 'pending'"
            ).fetchall()
        assert len(reviews) >= 1

    def test_functional_same_target_no_review(self, contradiction_store):
        """Same functional target → no contradiction."""
        config = OntologyConfig(enabled=True)
        result = check_contradictions(
            contradiction_store,
            source_entity_id="e-miguel",
            relation="located_in",
            target_entity_id="e-paris",  # same as existing
            config=config,
        )
        assert result is False


class TestMultiValuedNoContradiction:
    def test_multi_valued_no_review(self, contradiction_store):
        """F2: Miguel uses Python + Miguel uses Go → no review (multi-valued)."""
        config = OntologyConfig(enabled=True)
        result = check_contradictions(
            contradiction_store,
            source_entity_id="e-miguel",
            relation="uses",
            target_entity_id="e-go",
            config=config,
        )
        assert result is False

        # No review rows for multi-valued
        with contradiction_store.connect() as db:
            reviews = db.execute(
                "SELECT * FROM memorant_ontology_review"
            ).fetchall()
        assert len(reviews) == 0


class TestCheckContradictionsForRelation:
    def _add_claim(self, store):
        from memorant import MemorantStore
        core = MemorantStore(store.db_path)
        core.init()
        return core.add_claim("contradiction test", source_pointer="test")

    def test_detects_contradiction(self, contradiction_store):
        """Two functional relations with different targets create a review."""
        config = OntologyConfig(enabled=True)
        cid = self._add_claim(contradiction_store)
        with contradiction_store.connect() as db:
            db.execute(
                "INSERT INTO memorant_ontology_relations (id, source_entity_id, target_entity_id, relation, source_claim_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ("r-london", "e-miguel", "e-london", "located_in", cid),
            )
            db.commit()

        result = check_contradictions_for_relation(
            contradiction_store, "r-lives-paris", "r-london", config
        )
        assert result is True

        with contradiction_store.connect() as db:
            reviews = db.execute(
                "SELECT * FROM memorant_ontology_review WHERE status = 'pending'"
            ).fetchall()
        assert len(reviews) >= 1

    def test_no_contradiction_for_multi_valued(self, contradiction_store):
        """Multi-valued relations do not create a review."""
        config = OntologyConfig(enabled=True)
        cid = self._add_claim(contradiction_store)
        with contradiction_store.connect() as db:
            db.execute(
                "INSERT INTO memorant_ontology_relations (id, source_entity_id, target_entity_id, relation, source_claim_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ("r-uses-go", "e-miguel", "e-go", "uses", cid),
            )
            db.commit()

        result = check_contradictions_for_relation(
            contradiction_store, "r-uses-python", "r-uses-go", config
        )
        assert result is False

    def test_duplicate_review_not_created(self, contradiction_store):
        """Calling twice for the same pair returns False the second time."""
        config = OntologyConfig(enabled=True)
        cid = self._add_claim(contradiction_store)
        with contradiction_store.connect() as db:
            db.execute(
                "INSERT INTO memorant_ontology_relations (id, source_entity_id, target_entity_id, relation, source_claim_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ("r-london", "e-miguel", "e-london", "located_in", cid),
            )
            db.commit()

        assert check_contradictions_for_relation(
            contradiction_store, "r-lives-paris", "r-london", config
        ) is True
        assert check_contradictions_for_relation(
            contradiction_store, "r-lives-paris", "r-london", config
        ) is False
