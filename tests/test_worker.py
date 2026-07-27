"""Tests for worker internals."""

from memorant import MemorantStore
from memorant_ontology.config import OntologyConfig
from memorant_ontology.store import OntologyStore
from memorant_ontology.worker import _write_results


class TestWriteResultsTrustTier:
    def test_entities_inherit_claim_trust_tier(self, tmp_path):
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig(enabled=True))
        store.init()
        cid = core.add_claim("Miguel works at Tier4IT", source_pointer="test", trust_tier="verified")

        result = {
            "entities": [
                {"id": "e1", "name": "Miguel", "entity_type": "person", "description": "dev"},
                {"id": "e2", "name": "Tier4IT", "entity_type": "organization", "description": "co"},
            ],
            "relations": [
                {"source": "Miguel", "target": "Tier4IT", "relation": "works_at", "confidence": 0.9}
            ],
            "prompt_version": "v1",
            "cost_usd": 0.001,
        }

        _write_results(store, cid, result, OntologyConfig(enabled=True), claim_trust_tier="verified")

        with store.connect() as db:
            entity = db.execute(
                "SELECT trust_tier FROM memorant_ontology_entities WHERE name = ?", ("Miguel",)
            ).fetchone()
            assert entity["trust_tier"] == "verified"
            relation = db.execute(
                "SELECT trust_tier FROM memorant_ontology_relations WHERE relation = ?", ("works_at",)
            ).fetchone()
            assert relation["trust_tier"] == "verified"

    def test_entities_default_to_derived_when_tier_missing(self, tmp_path):
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig(enabled=True))
        store.init()
        cid = core.add_claim("Miguel works at Tier4IT", source_pointer="test")

        result = {
            "entities": [
                {"id": "e1", "name": "Miguel", "entity_type": "person", "description": "dev"},
            ],
            "relations": [],
            "prompt_version": "v1",
            "cost_usd": 0.0,
        }

        _write_results(store, cid, result, OntologyConfig(enabled=True), claim_trust_tier=None)

        with store.connect() as db:
            entity = db.execute(
                "SELECT trust_tier FROM memorant_ontology_entities WHERE name = ?", ("Miguel",)
            ).fetchone()
            assert entity["trust_tier"] == "derived"

    def test_eager_trust_propagation_updates_existing_entity(self, tmp_path):
        """NEW: eager trust propagation updates existing entity tier on reinforcement."""
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig(enabled=True, trust_propagation="eager"))
        store.init()
        cid = core.add_claim("Miguel works at Tier4IT", source_pointer="test", trust_tier="verified")

        result = {
            "entities": [
                {"id": "e1", "name": "Miguel", "entity_type": "person", "description": "dev"},
            ],
            "relations": [],
            "prompt_version": "v1",
            "cost_usd": 0.001,
        }

        # First write: entity gets "verified"
        _write_results(store, cid, result, OntologyConfig(enabled=True, trust_propagation="eager"), claim_trust_tier="verified")

        # Second write with different tier: eager mode should update
        cid2 = core.add_claim("Miguel works at Tier4IT again", source_pointer="test", trust_tier="operator")
        _write_results(store, cid2, result, OntologyConfig(enabled=True, trust_propagation="eager"), claim_trust_tier="operator")

        with store.connect() as db:
            entity = db.execute(
                "SELECT trust_tier, reinforcement_count FROM memorant_ontology_entities WHERE name = ?", ("Miguel",)
            ).fetchone()
            assert entity["trust_tier"] == "operator"
            assert entity["reinforcement_count"] == 2

    def test_conservative_trust_preserves_existing_tier(self, tmp_path):
        """NEW: conservative trust propagation preserves existing entity tier."""
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig(enabled=True, trust_propagation="conservative"))
        store.init()
        cid = core.add_claim("Miguel works at Tier4IT", source_pointer="test", trust_tier="verified")

        result = {
            "entities": [
                {"id": "e1", "name": "Miguel", "entity_type": "person", "description": "dev"},
            ],
            "relations": [],
            "prompt_version": "v1",
            "cost_usd": 0.001,
        }

        _write_results(store, cid, result, OntologyConfig(enabled=True, trust_propagation="conservative"), claim_trust_tier="verified")

        # Second write with different tier: conservative should preserve original
        cid2 = core.add_claim("Miguel works at Tier4IT again", source_pointer="test", trust_tier="untrusted")
        _write_results(store, cid2, result, OntologyConfig(enabled=True, trust_propagation="conservative"), claim_trust_tier="untrusted")

        with store.connect() as db:
            entity = db.execute(
                "SELECT trust_tier, reinforcement_count FROM memorant_ontology_entities WHERE name = ?", ("Miguel",)
            ).fetchone()
            assert entity["trust_tier"] == "verified"
            assert entity["reinforcement_count"] == 2
