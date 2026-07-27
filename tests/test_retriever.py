"""Tests for retriever — normalized lookup, is_valid filter, LIMIT, provenance, perf."""

import time
import uuid
from pathlib import Path

import pytest

from memorant import MemorantStore
from memorant_ontology.config import OntologyConfig
from memorant_ontology.store import OntologyStore
from memorant_ontology.retriever import OntologyRetriever


@pytest.fixture
def populated_store(tmp_path: Path):
    """Store with entities and relations pre-loaded."""
    db_path = tmp_path / "test.db"
    core = MemorantStore(db_path)
    core.init()
    store = OntologyStore(db_path, OntologyConfig(enabled=True))
    store.init()

    # Add a claim
    cid = core.add_claim("Miguel manages Tier4IT and uses Python", source_pointer="test")

    with store.connect() as db:
        # Entities
        db.execute(
            "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type, first_seen_claim_id, last_seen_claim_id) "
            "VALUES ('e1', 'Miguel', 'miguel', 'person', ?, ?)", (cid, cid)
        )
        db.execute(
            "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type, first_seen_claim_id, last_seen_claim_id) "
            "VALUES ('e2', 'Tier4IT', 'tier4it', 'organization', ?, ?)", (cid, cid)
        )
        db.execute(
            "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type, first_seen_claim_id, last_seen_claim_id) "
            "VALUES ('e3', 'Python', 'python', 'tool', ?, ?)", (cid, cid)
        )
        db.execute(
            "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type, is_valid) "
            "VALUES ('e4', 'OldEntity', 'oldentity', 'concept', 0)"
        )

        # Relations
        db.execute(
            "INSERT INTO memorant_ontology_relations (id, source_entity_id, target_entity_id, relation, source_claim_id) "
            "VALUES ('r1', 'e1', 'e2', 'manages', ?)", (cid,)
        )
        db.execute(
            "INSERT INTO memorant_ontology_relations (id, source_entity_id, target_entity_id, relation, source_claim_id) "
            "VALUES ('r2', 'e1', 'e3', 'uses', ?)", (cid,)
        )
        db.commit()

    return store


class TestFindEntities:
    def test_normalized_lookup(self, populated_store):
        """m7: find_entities(name='Miguel') matches stored 'Miguel'."""
        retriever = OntologyRetriever(populated_store)
        results = retriever.find_entities("Miguel")
        assert len(results) == 1
        assert results[0]["name"] == "Miguel"

    def test_case_insensitive(self, populated_store):
        retriever = OntologyRetriever(populated_store)
        results = retriever.find_entities("miguel")
        assert len(results) == 1

    def test_is_valid_filter(self, populated_store):
        """M6: is_valid=0 entities not returned."""
        retriever = OntologyRetriever(populated_store)
        results = retriever.find_entities("OldEntity")
        assert len(results) == 0

    def test_no_match(self, populated_store):
        retriever = OntologyRetriever(populated_store)
        results = retriever.find_entities("Nonexistent")
        assert len(results) == 0


class TestFindRelated:
    def test_basic_walk(self, populated_store):
        retriever = OntologyRetriever(populated_store)
        results = retriever.find_related("Miguel", depth=1)
        assert len(results) >= 1
        # Should find relations from Miguel
        relations = {r["relation"] for r in results}
        assert "manages" in relations or "uses" in relations

    def test_limit_enforced(self, populated_store):
        """LIMIT 50 enforced."""
        # Add many relations — use a real claim ID from the DB
        with populated_store.connect() as db:
            cid = db.execute("SELECT id FROM claim_units LIMIT 1").fetchone()["id"]
            for i in range(100):
                eid = f"extra-{i}"
                db.execute(
                    "INSERT INTO memorant_ontology_entities (id, name, name_normalized, entity_type) "
                    "VALUES (?, ?, ?, 'concept')", (eid, f"Extra{i}", f"extra{i}")
                )
                db.execute(
                    "INSERT INTO memorant_ontology_relations (id, source_entity_id, target_entity_id, relation, source_claim_id) "
                    "VALUES (?, 'e1', ?, 'uses', ?)", (f"rr-{i}", eid, cid)
                )
            db.commit()

        retriever = OntologyRetriever(populated_store)
        results = retriever.find_related("Miguel", depth=1, limit=50)
        assert len(results) <= 50

    def test_with_provenance(self, populated_store):
        retriever = OntologyRetriever(populated_store)
        results = retriever.find_related("Miguel", depth=1, with_provenance=True)
        assert len(results) >= 1
        # Should have source_claim_id
        assert "source_claim_id" in results[0]

    def test_depth_capped_at_3(self, populated_store):
        retriever = OntologyRetriever(populated_store)
        # depth=10 should be capped at 3
        results = retriever.find_related("Miguel", depth=10)
        for r in results:
            assert r["hops"] <= 3

    def test_invalid_entities_excluded(self, populated_store):
        """Walk through invalid entities should not happen."""
        # e4 is invalid, create a relation through it using a real claim ID
        with populated_store.connect() as db:
            cid = db.execute("SELECT id FROM claim_units LIMIT 1").fetchone()["id"]
            db.execute(
                "INSERT INTO memorant_ontology_relations (id, source_entity_id, target_entity_id, relation, source_claim_id, is_valid) "
                "VALUES ('r-invalid', 'e1', 'e4', 'uses', ?, 0)", (cid,)
            )
            db.commit()

        retriever = OntologyRetriever(populated_store)
        results = retriever.find_related("Miguel", depth=1)
        # Invalid relation should not appear
        target_ids = {r["target_id"] for r in results}
        assert "e4" not in target_ids

    def test_index_exists(self, populated_store):
        """Verify idx_ontology_relations_source_target index exists."""
        with populated_store.connect() as db:
            indexes = {row[1] for row in db.execute(
                "SELECT * FROM sqlite_master WHERE type='index'"
            ).fetchall()}
        assert "idx_ontology_relations_source_target" in indexes

    def test_cycle_detection_stops_infinite_loop(self, populated_store):
        """M6: a cycle A→B→A should terminate and not recurse infinitely."""
        with populated_store.connect() as db:
            cid = db.execute("SELECT id FROM claim_units LIMIT 1").fetchone()["id"]
            # Make e3 point back to e1 to form a cycle
            db.execute(
                "INSERT INTO memorant_ontology_relations "
                "(id, source_entity_id, target_entity_id, relation, source_claim_id) "
                "VALUES ('r-cycle', 'e3', 'e1', 'influences', ?)",
                (cid,),
            )
            db.commit()

        retriever = OntologyRetriever(populated_store)
        # Without cycle detection this could blow up; with it we should
        # only see the forward edges from Miguel and stop when we revisit e1.
        results = retriever.find_related("Miguel", depth=3, limit=100)
        # Forward edges only (the cycle back to Miguel is pruned by the path guard)
        assert len(results) == 2
        for r in results:
            assert r["hops"] <= 3
            assert not (r["source_id"] == "e3" and r["target_id"] == "e1")

    def test_self_loop_does_not_cause_duplicate_edges(self, populated_store):
        """A→A self-loop should not produce duplicate edges."""
        with populated_store.connect() as db:
            cid = db.execute("SELECT id FROM claim_units LIMIT 1").fetchone()["id"]
            db.execute(
                "INSERT INTO memorant_ontology_relations "
                "(id, source_entity_id, target_entity_id, relation, source_claim_id) "
                "VALUES ('r-self', 'e1', 'e1', 'self_manages', ?)",
                (cid,),
            )
            db.commit()

        retriever = OntologyRetriever(populated_store)
        results = retriever.find_related("Miguel", depth=1)
        # The self-loop should appear exactly once after deduplication
        self_loops = [r for r in results if r["source_id"] == "e1" and r["target_id"] == "e1"]
        assert len(self_loops) == 1


class TestRetrieverPerf:
    def test_perf_1000_queries(self, populated_store):
        """p99 < 50ms for 1000 queries (relaxed for Windows I/O)."""
        retriever = OntologyRetriever(populated_store)
        latencies = []
        for _ in range(1000):
            start = time.perf_counter()
            retriever.find_entities("Miguel")
            latencies.append((time.perf_counter() - start) * 1000)

        latencies.sort()
        p99 = latencies[989]  # 99th percentile
        assert p99 < 50, f"p99 latency {p99:.2f}ms exceeds 50ms"
