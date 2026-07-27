"""Tests for the memorant_ontology CLI."""

import json
from pathlib import Path

import pytest

from memorant import MemorantStore
from memorant_ontology import _PATCHED
from memorant_ontology.cli import main
from memorant_ontology.config import OntologyConfig
from memorant_ontology.store import OntologyStore


@pytest.fixture
def populated_db(tmp_path: Path):
    """Return a DB path with an initialized core and ontology store."""
    db_path = tmp_path / "test.db"
    core = MemorantStore(db_path)
    core.init()
    store = OntologyStore(db_path, OntologyConfig(enabled=True))
    store.init()
    return db_path


class TestImportJsonl:
    def test_imports_claims_and_patches_add_claim(self, populated_db, tmp_path, restore_patch):
        """M5: import-jsonl calls patch_memorant_store and loads claims."""
        db_path = populated_db
        jsonl_path = tmp_path / "claims.jsonl"
        jsonl_path.write_text(
            json.dumps({"content": "Miguel uses Python", "trust_tier": "verified"}) + "\n"
            + json.dumps({"content": "Andre manages Tier4IT"}) + "\n"
        )

        main(["--db", str(db_path), "import-jsonl", str(jsonl_path)])

        core = MemorantStore(db_path)
        with core.connect() as db:
            rows = db.execute("SELECT content FROM claim_units ORDER BY id").fetchall()
        contents = [r["content"] for r in rows]
        assert "Miguel uses Python" in contents
        assert "Andre manages Tier4IT" in contents

    def test_import_creates_ontology_queue_rows(self, populated_db, tmp_path, restore_patch):
        """M5: import-jsonl with patched add_claim enqueues claims for extraction."""
        db_path = populated_db
        jsonl_path = tmp_path / "claims.jsonl"
        jsonl_path.write_text(
            json.dumps({"content": "Miguel uses Python"}) + "\n"
        )

        main(["--db", str(db_path), "import-jsonl", str(jsonl_path)])

        store = OntologyStore(db_path, OntologyConfig(enabled=True))
        with store.connect() as db:
            rows = db.execute("SELECT * FROM memorant_ontology_queue").fetchall()
        assert len(rows) == 1
        assert rows[0]["state"] == "pending"

    def test_import_skips_malformed_lines(self, populated_db, tmp_path, restore_patch):
        """Malformed lines are skipped, valid lines still imported."""
        db_path = populated_db
        jsonl_path = tmp_path / "claims.jsonl"
        jsonl_path.write_text(
            json.dumps({"content": "Valid claim"}) + "\n"
            "not valid json\n"
            + json.dumps({"content": "Another valid"}) + "\n"
        )

        main(["--db", str(db_path), "import-jsonl", str(jsonl_path)])

        core = MemorantStore(db_path)
        with core.connect() as db:
            rows = db.execute("SELECT content FROM claim_units ORDER BY id").fetchall()
        contents = [r["content"] for r in rows]
        assert "Valid claim" in contents
        assert "Another valid" in contents

    def test_import_skips_invalid_trust_tier(self, populated_db, tmp_path, restore_patch, capsys):
        """NEW: invalid trust_tier values print warning and continue import."""
        db_path = populated_db
        jsonl_path = tmp_path / "claims.jsonl"
        jsonl_path.write_text(
            json.dumps({"content": "Valid 1", "trust_tier": "verified"}) + "\n"
            + json.dumps({"content": "Bad trust", "trust_tier": "INVALID"}) + "\n"
            + json.dumps({"content": "Valid 2"}) + "\n"
        )

        main(["--db", str(db_path), "import-jsonl", str(jsonl_path)])

        core = MemorantStore(db_path)
        with core.connect() as db:
            rows = db.execute("SELECT content FROM claim_units ORDER BY id").fetchall()
        contents = [r["content"] for r in rows]
        assert "Valid 1" in contents
        assert "Valid 2" in contents
        # "Bad trust" should NOT be in the DB (check constraint violation)
        assert "Bad trust" not in contents
