"""Tests for monkey-patch — add_claim hook, dedup-return, exception isolation."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from memorant import MemorantStore
from memorant_ontology.config import OntologyConfig
from memorant_ontology.store import OntologyStore
from memorant_ontology import patch_memorant_store, _PATCHED, _PER_DB_ENABLED


@pytest.fixture
def patched_env(tmp_path: Path):
    """Set up a patched MemorantStore environment."""
    db_path = tmp_path / "test.db"
    # Reset module-level state
    _PATCHED.clear()
    _PATCHED.append(False)
    _PER_DB_ENABLED.clear()
    # Save original add_claim to restore in teardown
    original_add_claim = MemorantStore.add_claim

    core = MemorantStore(db_path)
    core.init()

    # Initialize ontology schema
    store = OntologyStore(db_path, OntologyConfig(enabled=True))
    store.init()

    yield core, store, db_path

    # Teardown: restore original add_claim to prevent leaking into other tests
    MemorantStore.add_claim = original_add_claim
    _PATCHED.clear()
    _PATCHED.append(False)
    _PER_DB_ENABLED.clear()


class TestPatchBasic:
    def test_add_claim_returns_id(self, patched_env):
        """add_claim returns a claim ID after patching."""
        core, store, db_path = patched_env
        with patch("memorant_ontology.config.OntologyConfig") as MockCfg:
            MockCfg.return_value = OntologyConfig(enabled=False)
            patch_memorant_store()
            cid = core.add_claim("Test claim", source_pointer="test")
        assert cid is not None
        assert len(cid) > 0

    def test_enqueue_on_enabled(self, patched_env):
        """When enabled, add_claim enqueues a queue row."""
        core, store, db_path = patched_env
        with patch("memorant_ontology.config.OntologyConfig") as MockCfg:
            MockCfg.return_value = OntologyConfig(enabled=True)
            patch_memorant_store()
            cid = core.add_claim("Test claim", source_pointer="test")

        with store.connect() as db:
            rows = db.execute(
                "SELECT * FROM memorant_ontology_queue WHERE claim_id = ?", (cid,)
            ).fetchall()
        assert len(rows) == 1

    def test_no_enqueue_when_disabled(self, patched_env):
        """When disabled, no queue row inserted."""
        core, store, db_path = patched_env
        with patch("memorant_ontology.config.OntologyConfig") as MockCfg:
            MockCfg.return_value = OntologyConfig(enabled=False)
            patch_memorant_store()
            cid = core.add_claim("Test claim", source_pointer="test")

        with store.connect() as db:
            rows = db.execute(
                "SELECT * FROM memorant_ontology_queue WHERE claim_id = ?", (cid,)
            ).fetchall()
        assert len(rows) == 0


class TestDedupReturn:
    def test_dedup_returns_same_id(self, patched_env):
        """F4: add_claim with same content returns same id; no new queue row."""
        core, store, db_path = patched_env
        with patch("memorant_ontology.config.OntologyConfig") as MockCfg:
            MockCfg.return_value = OntologyConfig(enabled=True)
            patch_memorant_store()
            cid1 = core.add_claim("Same content", source_pointer="a")
            cid2 = core.add_claim("Same content", source_pointer="b")

        assert cid1 == cid2

        with store.connect() as db:
            rows = db.execute(
                "SELECT * FROM memorant_ontology_queue WHERE claim_id = ?", (cid1,)
            ).fetchall()
        # Should be exactly 1 queue row (not 2)
        assert len(rows) == 1

    def test_reinforcement_no_enqueue(self, patched_env):
        """M10: reinforcement does not clear ontology_processed_at; no new queue row."""
        core, store, db_path = patched_env
        with patch("memorant_ontology.config.OntologyConfig") as MockCfg:
            MockCfg.return_value = OntologyConfig(enabled=True)
            patch_memorant_store()
            cid = core.add_claim("Reinforce me", source_pointer="a")

        # Mark as processed
        with store.connect() as db:
            db.execute(
                "UPDATE claim_units SET ontology_processed_at = datetime('now') WHERE id = ?",
                (cid,),
            )
            db.commit()

        # Reinforce — should NOT enqueue (processed already)
        with patch("memorant_ontology.config.OntologyConfig") as MockCfg:
            MockCfg.return_value = OntologyConfig(enabled=True)
            patch_memorant_store()
            cid2 = core.add_claim("Reinforce me", source_pointer="b")

        assert cid == cid2

        # ontology_processed_at unchanged
        with store.connect() as db:
            row = db.execute(
                "SELECT ontology_processed_at, reinforcement_count FROM claim_units WHERE id = ?",
                (cid,),
            ).fetchone()
        assert row["ontology_processed_at"] is not None
        assert row["reinforcement_count"] >= 1


class TestDoublePatchGuard:
    def test_no_double_wrap(self, patched_env):
        """m12: patch_memorant_store called twice → no double-wrap."""
        core, store, db_path = patched_env
        _PATCHED.clear()
        _PATCHED.append(False)

        with patch("memorant_ontology.config.OntologyConfig") as MockCfg:
            MockCfg.return_value = OntologyConfig(enabled=False)
            patch_memorant_store()
            first_hook = MemorantStore.add_claim
            patch_memorant_store()
            second_hook = MemorantStore.add_claim

        assert first_hook is second_hook


class TestExceptionIsolation:
    def test_hook_exception_doesnt_break_add_claim(self, patched_env):
        """Hook exception: add_claim still returns the id, dead-letter written."""
        core, store, db_path = patched_env
        _PATCHED.clear()
        _PATCHED.append(False)

        with patch("memorant_ontology.config.OntologyConfig") as MockCfg:
            MockCfg.return_value = OntologyConfig(enabled=True)
            # Mock _ext_for_db_path to raise
            with patch("memorant_ontology._ext_for_db_path", side_effect=RuntimeError("boom")):
                patch_memorant_store()
                cid = core.add_claim("Test with error", source_pointer="test")

        assert cid is not None

        # Dead-letter file should exist
        dl = db_path.with_suffix(".dead-letter.jsonl")
        assert dl.exists()
        content = dl.read_text(encoding="utf-8")
        assert "boom" in content

    def test_add_claim_returns_id_on_integrity_error(self, patched_env):
        """sqlite3.IntegrityError (already queued) → add_claim still returns id."""
        core, store, db_path = patched_env
        _PATCHED.clear()
        _PATCHED.append(False)

        with patch("memorant_ontology.config.OntologyConfig") as MockCfg:
            MockCfg.return_value = OntologyConfig(enabled=True)
            patch_memorant_store()
            # First call succeeds and enqueues
            cid1 = core.add_claim("Integrity test", source_pointer="a")
            # Second call — same content → dedup → IntegrityError on queue → should not crash
            cid2 = core.add_claim("Integrity test", source_pointer="b")

        assert cid1 == cid2
