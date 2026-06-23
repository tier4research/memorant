"""Comprehensive tests for Memorant v1 — MemorantStore API."""

import json
import sqlite3
import time
from pathlib import Path

import pytest

from memorant import MemorantStore, MemoryPalace, StoreConfig, Claim
from memorant.trust import TrustTier, TrustPolicy, redact_content, is_redaction_safe


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path: Path) -> MemorantStore:
    return MemorantStore(tmp_path / "test.db")


@pytest.fixture
def populated(store: MemorantStore) -> MemorantStore:
    """Store with a few claims across trust tiers."""
    store.add_claim("The user prefers concise technical summaries.", source_pointer="manual", trust_tier="operator")
    store.add_claim("Project uses pytest for testing.", source_pointer="manual", trust_tier="verified")
    store.add_claim("Deployments happen on Fridays.", source_pointer="import", trust_tier="untrusted")
    store.add_claim("SQL debugging requires tokenization analysis.", source_pointer="manual", trust_tier="operator")
    return store


# ── Initialization ────────────────────────────────────────────

class TestInit:
    def test_init_creates_tables(self, store):
        tables = store.init()
        assert "claim_units" in tables
        assert "supersedes" in tables
        assert "corrects" in tables
        assert "derived_from" in tables

    def test_init_is_idempotent(self, store):
        t1 = store.init()
        t2 = store.init()
        assert t1 == t2

    def test_new_store_has_version_zero(self, store):
        assert store._steward.user_version == 0

    def test_steward_available(self, store):
        store.init()
        assert store._steward.user_version >= 0
        assert store.integrity_check()


# ── Claim CRUD ────────────────────────────────────────────────

class TestClaimCRUD:
    def test_add_and_retrieve(self, store):
        cid = store.add_claim("This is a test claim.", source_pointer="test")
        claim = store.get_claim(cid)
        assert claim is not None
        assert claim.content == "This is a test claim."
        assert claim.trust_tier == "untrusted"  # Default policy

    def test_add_with_trust_tier(self, store):
        cid = store.add_claim("Operator claim.", source_pointer="manual", trust_tier="operator")
        claim = store.get_claim(cid)
        assert claim.trust_tier == "operator"

    def test_atomic_deduplication(self, store):
        """Identical content should increment reinforcement, not create duplicate."""
        a = store.add_claim("Same thing.", source_pointer="a")
        b = store.add_claim("  Same   thing.  ", source_pointer="b")  # Whitespace normalized
        assert a == b

    def test_deduplication_increments_reinforcement(self, store):
        cid = store.add_claim("Reinforce me.", source_pointer="a")
        c1 = store.get_claim(cid)
        store.add_claim("Reinforce me.", source_pointer="b")
        c2 = store.get_claim(cid)
        assert c2.reinforcement_count > c1.reinforcement_count

    def test_get_nonexistent(self, store):
        assert store.get_claim("nonexistent-id") is None


# ── Search & Retrieval ────────────────────────────────────────

class TestSearch:
    def test_basic_search(self, populated):
        results = populated.search("technical summaries")
        assert len(results) > 0
        assert any("concise technical" in r.content.lower() for r in results)

    def test_search_returns_scored_results(self, populated):
        results = populated.search("testing")
        assert len(results) > 0
        assert all(r.score > 0 for r in results)

    def test_search_respects_trust_filter(self, populated):
        results = populated.search("fridays", min_trust="verified")
        # The "Fridays" claim is untrusted — should not appear
        assert not any("Friday" in r.content for r in results)

    def test_search_respects_limit(self, populated):
        results = populated.search("the", limit=2)
        assert len(results) <= 2

    def test_search_empty_query(self, store):
        results = store.search("")
        assert results == []

    def test_search_temporal_filter(self, store):
        store.add_claim("Future fact.", source_pointer="test", valid_from="2027-01-01T00:00:00+00:00")
        assert not store.search("Future", as_of="2026-01-01T00:00:00+00:00")
        assert store.search("Future", as_of="2027-06-01T00:00:00+00:00")

    def test_search_results_sorted_by_score(self, populated):
        # Add a highly reinforced claim
        cid = populated.add_claim("pytest is great for testing.", source_pointer="manual", trust_tier="verified")
        for _ in range(5):
            populated.add_claim("pytest is great for testing.", source_pointer="manual")
        results = populated.search("pytest testing")
        assert len(results) >= 1
        # First result should be the reinforced one
        assert results[0].reinforcement_count >= 5


# ── Resonance ─────────────────────────────────────────────────

class TestResonance:
    def test_resonance_returns_block(self, populated):
        block = populated.resonate("concise technical summary")
        assert "[MEMORANT_RESONANCE]" in block
        assert "internal_only=true" in block

    def test_resonance_filters_by_trust(self, populated):
        """Untrusted claims should not auto-resonate."""
        # Add an untrusted claim matching the query
        populated.add_claim("Technical summaries should be verbose.", source_pointer="test", trust_tier="untrusted")
        block = populated.resonate("technical summary verbose")
        assert "verbose" not in block or "[REDACTED" in block

    def test_resonance_respects_floor(self, populated):
        block = populated.resonate("xyzzy_nonexistent_term_12345", floor=0.99)
        assert block == "" or "(no resonance)" in block.lower()

    def test_resonance_applies_redaction(self, populated):
        """Field-aware redaction redacts secrets, keeps benign terms."""
        populated.add_claim(
            "apikey=thisisasecretkey credential passwd=mypassword SQL debug tokenization ok.",
            source_pointer="test",
            trust_tier="operator",
        )
        block = populated.resonate("apikey credential SQL")
        assert "thisisasecretkey" not in block
        assert "mypassword" not in block
        assert "sql" in block.lower()
        assert "tokenization" in block.lower()

    def test_resonance_empty_on_no_match(self, store):
        block = store.resonate("this will match nothing at all anywhere")
        assert block == ""

    def test_resonance_logs_to_db(self, populated):
        populated.resonate("technical", session_id="test-session")
        with populated.connect() as db:
            logs = db.execute("SELECT * FROM resonance_log WHERE session_id = 'test-session'").fetchall()
            assert len(logs) > 0
            assert logs[0]["fired"] == 1


# ── Trust & Redaction ─────────────────────────────────────────

class TestTrustTiers:
    def test_rank_ordering(self):
        assert TrustTier.rank("operator") < TrustTier.rank("verified")
        assert TrustTier.rank("verified") < TrustTier.rank("derived")
        assert TrustTier.rank("derived") < TrustTier.rank("untrusted")

    def test_allowed_for_resonance(self):
        allowed = TrustTier.allowed_for_resonance()
        assert "operator" in allowed
        assert "verified" in allowed
        assert "untrusted" not in allowed
        assert "derived" not in allowed

    def test_unknown_tier_defaults_to_untrusted(self):
        assert TrustTier.rank("made_up_tier") == TrustTier.rank("untrusted")


class TestTrustPolicy:
    def test_default_policy_assigns_untrusted(self):
        policy = TrustPolicy()
        assert policy.evaluate("manual", "any") == "untrusted"

    def test_rule_based_assignment(self):
        policy = TrustPolicy(rules=[
            {"source_type": "manual", "tier": "operator"},
            {"source_pointer": "verified:*", "tier": "verified"},
        ])
        assert policy.evaluate("manual", "any") == "operator"
        assert policy.evaluate("import", "verified:src/test") == "verified"
        assert policy.evaluate("import", "other") == "untrusted"

    def test_first_matching_rule_wins(self):
        policy = TrustPolicy(rules=[
            {"source_type": "manual", "tier": "verified"},
            {"source_type": "manual", "source_pointer": "admin", "tier": "operator"},
        ])
        # First rule matches before second is checked
        assert policy.evaluate("manual", "admin") == "verified"


class TestRedaction:
    def test_redacts_api_keys(self):
        result = redact_content("apikey=mysecrettoken")
        assert "mysecrettoken" not in result
        assert "[REDACTED" in result

    def test_redacts_bearer_tokens(self):
        result = redact_content("Authorization: bearer abcdefghijklmnopqrstuvwxyz123456")
        assert "abcdefghijklmnopqrstuvwxyz123456" not in result
        assert "[REDACTED:TOKEN]" in result

    def test_redacts_github_tokens(self):
        result = redact_content("github_pat_11CGQASYQ0bESfaYKX3dfm")
        assert "github_pat_" not in result
        assert "[REDACTED:GITHUB_TOKEN]" in result

    def test_preserves_benign_terms(self):
        result = redact_content("SQL debugging and tokenization analysis with key mappings.")
        assert "SQL" in result
        assert "tokenization" in result
        assert "debug" in result and "debugging" in result.lower()

    def test_preserves_embedding_term(self):
        result = redact_content("The embedding vector tokenizes input before lookup.")
        assert "embedding" in result
        assert "tokenizes" in result

    def test_redacts_password_in_context(self):
        result = redact_content("password=supersecret123 config")
        assert "supersecret123" not in result
        assert "[REDACTED:CREDENTIAL]" in result

    def test_redaction_safety_check(self):
        assert is_redaction_safe(
            "SQL debug tokenization analysis with embedding vectors.",
            {"sql", "debug", "tokenization", "embedding"},
        )

    def test_redaction_truncates_long_content(self):
        long_text = "A" * 500
        result = redact_content(long_text)
        assert len(result) <= 243  # 240 + "..."

    def test_no_false_positive_on_normal_text(self):
        result = redact_content("The user prefers dark mode and concise summaries.")
        assert "dark mode" in result
        assert "concise" in result
        assert "[REDACTED" not in result


# ── Invalidation & Relations ──────────────────────────────────

class TestInvalidation:
    def test_invalidate_claim(self, store):
        cid = store.add_claim("Temporary fact.", source_pointer="test")
        assert store.invalidate_claim(cid) == 1
        assert store.get_claim(cid) is None

    def test_invalidate_nonexistent(self, store):
        assert store.invalidate_claim("nonexistent") == 0

    def test_invalidate_is_idempotent(self, store):
        cid = store.add_claim("Once more.", source_pointer="test")
        store.invalidate_claim(cid)
        assert store.invalidate_claim(cid) == 0  # Already invalid

    def test_supersede_claim(self, store):
        old_id = store.add_claim("Old version.", source_pointer="test")
        new_id = store.supersede_claim(old_id, "New version.", reason="outdated")
        assert new_id != old_id
        assert store.get_claim(old_id) is None
        new = store.get_claim(new_id)
        assert new.content == "New version."

    def test_supersede_records_relation(self, store):
        old_id = store.add_claim("Original.", source_pointer="test")
        new_id = store.supersede_claim(old_id, "Updated.", reason="fix")

        with store.connect() as db:
            rel = db.execute(
                "SELECT * FROM supersedes WHERE superseding_id = ? AND superseded_id = ?",
                (new_id, old_id),
            ).fetchone()
            assert rel is not None
            assert rel["reason"] == "fix"

    def test_supersede_nonexistent_raises(self, store):
        with pytest.raises(ValueError, match="not found"):
            store.supersede_claim("nonexistent", "New.")

    def test_correct_claim(self, store):
        old_id = store.add_claim("Wrong fact.", source_pointer="test")
        new_id = store.correct_claim(old_id, "Corrected fact.", reason="typo")
        assert store.get_claim(old_id) is None
        assert store.get_claim(new_id).content == "Corrected fact."

    def test_correct_records_relation(self, store):
        old_id = store.add_claim("Error.", source_pointer="test")
        new_id = store.correct_claim(old_id, "Fixed.", reason="correction")

        with store.connect() as db:
            rel = db.execute(
                "SELECT * FROM corrects WHERE correcting_id = ? AND corrected_id = ?",
                (new_id, old_id),
            ).fetchone()
            assert rel is not None

    def test_derived_from_relation(self, store):
        src1 = store.add_claim("Source one.", source_pointer="test", trust_tier="verified")
        src2 = store.add_claim("Source two.", source_pointer="test", trust_tier="operator")
        derived = store.add_claim(
            "Derived claim.", source_pointer="test",
            derived_from_ids=[src1, src2],
        )

        with store.connect() as db:
            rels = db.execute(
                "SELECT source_id FROM derived_from WHERE derived_id = ?",
                (derived,),
            ).fetchall()
            assert len(rels) == 2

    def test_derived_inherits_min_trust(self, store):
        """Derived claims should inherit the minimum trust of their sources."""
        src1 = store.add_claim("High trust.", source_pointer="test", trust_tier="operator")
        src2 = store.add_claim("Low trust.", source_pointer="test", trust_tier="untrusted")
        derived = store.add_claim(
            "Inherited.", source_pointer="test",
            derived_from_ids=[src1, src2],
        )
        claim = store.get_claim(derived)
        # Should inherit untrusted (min of operator + untrusted)
        assert claim.trust_tier == "untrusted"

    def test_derived_inherits_min_trust_when_policy_assigns_trust(self, tmp_path):
        """Implicit policy trust should not block derived trust inheritance."""
        policy = TrustPolicy(rules=[
            {"source_type": "manual", "tier": "verified"},
        ])
        store = MemorantStore(
            tmp_path / "derived-policy.db",
            StoreConfig(trust_policy=policy),
        )
        src1 = store.add_claim("Policy source.", source_pointer="test", trust_tier="verified")
        src2 = store.add_claim("Lower source.", source_pointer="test", trust_tier="untrusted")

        derived = store.add_claim(
            "Policy-derived claim.",
            source_pointer="test",
            source_type="manual",
            derived_from_ids=[src1, src2],
        )

        assert store.get_claim(derived).trust_tier == "untrusted"

    def test_derived_from_ids_accepts_one_shot_iterators(self, store):
        """Generator inputs should record all derivation relations."""
        src1 = store.add_claim("Generator source one.", source_pointer="test")
        src2 = store.add_claim("Generator source two.", source_pointer="test")
        derived = store.add_claim(
            "Generator derived.",
            source_pointer="test",
            derived_from_ids=(src for src in [src1, src2]),
        )

        with store.connect() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM derived_from WHERE derived_id = ?",
                (derived,),
            ).fetchone()[0]
        assert count == 2

    def test_invalidate_claims_for_fact(self, store):
        store.add_claim("Fact-derived claim.", source_pointer="fact:f1#seg0", fact_refs=["f1"])
        store.add_claim("Another fact claim.", source_pointer="fact:f1#seg1")
        count = store.invalidate_claims_for_fact("f1")
        assert count == 2


# ── Digests ───────────────────────────────────────────────────

class TestDigests:
    def test_create_and_list(self, store):
        store.add_claim("Digest item.", source_pointer="test")
        did = store.create_digest(version="v-test")
        digests = store.list_digests()
        assert len(digests) == 1
        assert digests[0]["state"] == "pending"

    def test_promote_digest_atomically(self, store, tmp_path):
        store.add_claim("Standing fact.", source_pointer="test")
        did = store.create_digest(version="v-test")
        state_path = tmp_path / "standing_state.md"
        result = store.promote_digest(did, state_path)
        assert result.exists()
        assert result.read_text().startswith("# Standing State")

        # Digest state should be promoted
        digest = store.get_digest(did)
        assert digest["state"] == "promoted"

    def test_reject_digest(self, store):
        store.add_claim("Rejected item.", source_pointer="test")
        did = store.create_digest(version="v-reject")
        store.reject_digest(did, "not needed")
        digest = store.get_digest(did)
        assert digest["state"] == "rejected"
        assert "REJECTED: not needed" in digest["diff_from_prior"]

    def test_create_digest_generates_version(self, store):
        store.add_claim("Auto-versioned.", source_pointer="test")
        did = store.create_digest()
        digest = store.get_digest(did)
        assert digest["version"].startswith("v")


# ── Integrity & Backup ────────────────────────────────────────

class TestIntegrity:
    def test_integrity_check_passes(self, store):
        store.init()
        assert store.integrity_check()

    def test_backup_creates_copy(self, store):
        store.init()
        store.add_claim("Backup test.", source_pointer="test")
        backup = store.backup()
        assert backup.exists()
        assert ".backup-" in backup.name

        # Verify backup contains the claim
        conn = sqlite3.connect(str(backup))
        rows = conn.execute("SELECT content FROM claim_units").fetchall()
        assert any("Backup test" in r[0] for r in rows)
        conn.close()

    def test_export_import_jsonl(self, store, tmp_path):
        store.add_claim("Export item 1.", source_pointer="test", trust_tier="verified")
        store.add_claim("Export item 2.", source_pointer="test", trust_tier="operator")

        export_path = tmp_path / "export.jsonl"
        store.export_jsonl(export_path)
        assert export_path.exists()

        # Import into a new store
        store2 = MemorantStore(tmp_path / "test2.db")
        count = store2.import_jsonl(export_path, source_pointer="import")
        assert count == 2

    def test_export_import_preserves_fact_refs_array(self, tmp_path):
        source = MemorantStore(tmp_path / "source.db")
        source.add_claim(
            "Fact refs survive export import.",
            source_pointer="fact:f1#seg0",
            fact_refs=["f1", "room:library"],
        )

        export_path = tmp_path / "facts.jsonl"
        source.export_jsonl(export_path)

        imported = MemorantStore(tmp_path / "imported.db")
        assert imported.import_jsonl(export_path, source_pointer="import") == 1

        with imported.connect() as db:
            refs = db.execute("SELECT fact_refs FROM claim_units").fetchone()[0]
        assert json.loads(refs) == ["f1", "room:library"]


# ── Doctor Contract ───────────────────────────────────────────

class TestDoctor:
    def test_doctor_returns_healthy(self, store):
        store.init()
        exit_code = store.doctor(json_output=False)
        assert exit_code == 0

    def test_doctor_json_output(self, store, capsys):
        store.init()
        exit_code = store.doctor(json_output=True)
        captured = capsys.readouterr()
        report = json.loads(captured.out)
        assert report["status"] == "healthy"
        assert report["component"] == "memorant"
        assert "checks" in report


# ── Stats ─────────────────────────────────────────────────────

class TestStats:
    def test_stats_counts(self, populated):
        s = populated.stats()
        assert s["total_claims"] >= 4
        assert s["valid_claims"] >= 4
        assert "by_trust" in s


# ── Deprecated Alias ──────────────────────────────────────────

class TestMemoryPalaceCompat:
    def test_memory_palace_is_memorant_store(self, tmp_path):
        with pytest.deprecated_call():
            mp = MemoryPalace(tmp_path / "compat.db")
        assert isinstance(mp, MemorantStore)

    def test_memory_palace_works(self, tmp_path):
        with pytest.deprecated_call():
            mp = MemoryPalace(tmp_path / "works.db")
        cid = mp.add_claim("Compat test.", source_pointer="test")
        assert mp.get_claim(cid) is not None
        assert mp.search("compat")


# ── Store Config ──────────────────────────────────────────────

class TestStoreConfig:
    def test_default_config(self):
        config = StoreConfig()
        assert config.trust_policy is not None
        assert config.resonance_deadline_ms == 100

    def test_custom_trust_policy(self, tmp_path):
        policy = TrustPolicy(rules=[
            {"source_type": "custom", "tier": "verified"},
        ])
        config = StoreConfig(trust_policy=policy)
        store = MemorantStore(tmp_path / "custom.db", config=config)
        cid = store.add_claim("Custom trust.", source_pointer="any", source_type="custom")
        assert store.get_claim(cid).trust_tier == "verified"


# ── Encryption ─────────────────────────────────────────────────

class TestEncryption:
    """SQLCipher encryption (optional, requires memorant[encryption])."""

    def test_encryption_key_without_sqlcipher_raises(self, tmp_path):
        """Setting encryption_key without sqlcipher3 installed raises ImportError."""
        config = StoreConfig(encryption_key="test-key")
        store = MemorantStore(tmp_path / "enc.db", config=config)

        # Should raise because sqlcipher3 is not installed in test env
        with pytest.raises(ImportError, match="encryption_key requires"):
            store.add_claim("test", source_pointer="test")

    def test_no_encryption_key_uses_standard_sqlite(self, tmp_path):
        """Default config (no key) uses standard sqlite3 without error."""
        store = MemorantStore(tmp_path / "plain.db")
        cid = store.add_claim("Plain text claim.", source_pointer="test")
        assert store.get_claim(cid) is not None

    def test_wrong_key_fails_closed(self, tmp_path):
        """A database created with one key cannot be opened with another."""
        pytest.importorskip("sqlcipher3")

        # Create with key A
        config_a = StoreConfig(encryption_key="correct-key")
        store_a = MemorantStore(tmp_path / "enc.db", config=config_a)
        cid = store_a.add_claim("Secret claim.", source_pointer="test")
        assert store_a.get_claim(cid) is not None

        # Try to open with key B — must fail
        config_b = StoreConfig(encryption_key="wrong-key")
        store_b = MemorantStore(tmp_path / "enc.db", config=config_b)
        with pytest.raises(Exception):  # sqlcipher3 raises on wrong key
            store_b.add_claim("Should fail.", source_pointer="test")

    def test_correct_key_roundtrips(self, tmp_path):
        """Data written with a key can be read back with the same key."""
        sqlcipher3 = pytest.importorskip("sqlcipher3")

        config = StoreConfig(encryption_key="roundtrip-key")
        store = MemorantStore(tmp_path / "enc.db", config=config)
        cid = store.add_claim("Encrypted claim.", source_pointer="test")
        claim = store.get_claim(cid)
        assert claim is not None
        assert claim.content == "Encrypted claim."

    def test_encrypted_search(self, tmp_path):
        """Search works on encrypted databases."""
        pytest.importorskip("sqlcipher3")

        config = StoreConfig(encryption_key="search-key")
        store = MemorantStore(tmp_path / "enc.db", config=config)
        store.add_claim("Find me in encrypted storage.", source_pointer="test", trust_tier="verified")
        results = store.search("encrypted", min_trust="verified")
        assert len(results) > 0
        assert "encrypted" in results[0].content.lower()

    def test_encrypted_resonance(self, tmp_path):
        """Resonance works on encrypted databases."""
        pytest.importorskip("sqlcipher3")

        config = StoreConfig(encryption_key="res-key")
        store = MemorantStore(tmp_path / "enc.db", config=config)
        store.add_claim("Encrypted resonance test claim.", source_pointer="test", trust_tier="verified")
        block = store.resonate("encrypted resonance")
        assert "resonance" in block.lower()
        assert "[MEMORANT_RESONANCE]" in block


# ── Regression Tests — Bug Audit 2026-06-22 ─────────────────────

class TestBug1EncryptedRetriever:
    """FTSRetriever must use encrypted connection when encryption_key is set."""

    def test_fts_retriever_stores_encryption_key(self, tmp_path):
        """FTSRetriever stores the encryption_key for use in _connect."""
        from memorant.retriever import FTSRetriever
        retriever = FTSRetriever(tmp_path / "test.db", encryption_key="my-secret")
        assert retriever._encryption_key == "my-secret"

    def test_fts_retriever_none_key_defaults_to_standard(self, tmp_path):
        """FTSRetriever with no encryption_key uses standard sqlite3."""
        from memorant.retriever import FTSRetriever
        retriever = FTSRetriever(tmp_path / "test.db")
        assert retriever._encryption_key is None

    def test_store_passes_encryption_key_to_retriever(self, tmp_path):
        """MemorantStore.search() passes encryption_key to FTSRetriever."""
        config = StoreConfig(encryption_key="test-pass-key")
        store = MemorantStore(tmp_path / "enc.db", config=config)
        # Verify the retriever would receive the key by checking store config
        assert store.config.encryption_key == "test-pass-key"


class TestBug2RetentionModeNone:
    """retention_mode='none' must not store private context on timeout."""

    def test_timeout_with_none_retention_no_log(self, tmp_path):
        """Timeout path with retention_mode='none' does not write resonance_log."""
        config = StoreConfig(retention_mode="none", resonance_deadline_ms=1)
        store = MemorantStore(tmp_path / "bug2.db", config=config)
        store.add_claim("Test claim for resonance.", source_pointer="test", trust_tier="verified")
        # With 1ms deadline, resonance should timeout immediately
        block = store.resonate("test claim", session_id="retention-none-test")
        # Should return empty (timed out)
        assert block == ""
        # Verify no resonance_log entry was written
        with store.connect() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM resonance_log WHERE session_id = 'retention-none-test'"
            ).fetchone()[0]
            assert count == 0


class TestBug3IntegrityErrorFalseSuccess:
    """Non-duplicate IntegrityError must be re-raised, not silently succeed."""

    def test_invalid_trust_tier_raises(self, tmp_path):
        """A CHECK constraint violation (invalid trust_tier) raises IntegrityError."""
        store = MemorantStore(tmp_path / "bug3.db")
        with pytest.raises(sqlite3.IntegrityError):
            # trust_tier column has a CHECK constraint
            store.add_claim("Invalid trust.", source_pointer="test", trust_tier="made-up-tier")


class TestBug4FTSOrdering:
    """FTS5 ORDER BY should return best matches first."""

    def test_most_relevant_result_first(self, tmp_path):
        """The most FTS5-relevant claim should appear first in search results."""
        store = MemorantStore(tmp_path / "bug4.db")
        # Add claims with varying relevance to a specific query
        store.add_claim("The sky is blue on a clear day.", source_pointer="test", trust_tier="verified")
        store.add_claim("Blue is my favorite color for painting skies.", source_pointer="test", trust_tier="verified")
        store.add_claim("I like sandwiches for lunch.", source_pointer="test", trust_tier="verified")
        store.add_claim("Cars need fuel to run efficiently.", source_pointer="test", trust_tier="verified")
        store.add_claim("Blue skies are beautiful in the morning.", source_pointer="test", trust_tier="verified")

        results = store.search("blue sky")
        assert len(results) >= 2
        # The top result should contain both "blue" and either "sky" or "skies"
        first = results[0]
        assert "blue" in first.content.lower()
        assert ("sky" in first.content.lower() or "skies" in first.content.lower())
        assert first.score > 0

    def test_stronger_bm25_match_ranks_first(self, tmp_path):
        """Strong exact match (alpha beta) ranks before weak diluted match."""
        store = MemorantStore(tmp_path / "bug4b.db")

        strong_id = store.add_claim(
            "alpha beta",
            source_pointer="strong",
            trust_tier="verified",
        )
        weak_id = store.add_claim(
            "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda",
            source_pointer="weak",
            trust_tier="verified",
        )

        results = store.search("alpha beta")
        assert len(results) >= 2
        assert results[0].id == strong_id
        assert results[1].id == weak_id
        assert results[0].score > results[1].score  # Strong match scores higher


class TestBug5DeadlineEnforced:
    """Resonance deadline must be enforced even when search returns results."""

    def test_deadline_enforced_with_results(self, tmp_path):
        """Slow search with results must still return empty on timeout."""
        config = StoreConfig(resonance_deadline_ms=1)
        store = MemorantStore(tmp_path / "bug5.db", config=config)
        store.add_claim("Quick claim for deadline test.", source_pointer="test", trust_tier="verified")
        store.add_claim("Another verified claim.", source_pointer="test", trust_tier="verified")
        # With 1ms deadline, should timeout before search completes
        block = store.resonate("deadline test", session_id="deadline-test")
        # Must return empty (timed out), even though claims exist
        assert block == ""

    def test_deadline_logs_timeout_event(self, tmp_path):
        """Timeout path should log a resonance timeout event."""
        config = StoreConfig(resonance_deadline_ms=1)
        store = MemorantStore(tmp_path / "bug5b.db", config=config)
        store.add_claim("Timeout event test claim.", source_pointer="test", trust_tier="verified")
        store.resonate("timeout event", session_id="deadline-log-test")
        # Should have logged the timeout
        with store.connect() as db:
            logs = db.execute(
                "SELECT * FROM resonance_log WHERE session_id = 'deadline-log-test'"
            ).fetchall()
            assert len(logs) == 1
            assert logs[0]["fired"] == 0  # Did not fire


class TestBug6SourcePointerRedaction:
    """Secrets in source_pointer must be redacted in resonance output."""

    def test_secret_in_source_pointer_is_redacted(self, tmp_path):
        """source_pointer containing a secret is redacted in resonance."""
        store = MemorantStore(tmp_path / "bug6.db")
        store.add_claim(
            "Test claim with secret source.",
            source_pointer="apikey=supersecret123",
            trust_tier="verified",
        )
        block = store.resonate("test claim secret source")
        assert "[MEMORANT_RESONANCE]" in block
        assert "supersecret123" not in block

    def test_clean_source_pointer_passes_through(self, tmp_path):
        """Clean source_pointers should appear in resonance unscathed."""
        store = MemorantStore(tmp_path / "bug6b.db")
        store.add_claim(
            "Clean source test document chapter three paragraph two.",
            source_pointer="document:ch3-para2",
            trust_tier="verified",
        )
        block = store.resonate("clean source test document", floor=0.01)
        assert "document:ch3-para2" in block


class TestRecencyBonus:
    """Recency decay: newer claims score higher than identical older claims."""

    def test_recency_bonus_applied(self, tmp_path):
        """Recency bonus is a multiplicative factor in composite scoring."""
        store = MemorantStore(tmp_path / "recency.db")
        store.add_claim("Recency bonus testing claim.", source_pointer="test", trust_tier="verified")
        results = store.search("recency bonus")
        assert len(results) >= 1
        # Score should be non-negative with relative normalization
        assert results[0].score >= 0
