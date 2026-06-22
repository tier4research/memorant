from pathlib import Path
from memorant import MemoryPalace

def test_add_search_resonate(tmp_path: Path):
    p = MemoryPalace(tmp_path / "p.db"); p.init(); p.add_claim("The user prefers concise technical summaries.", source_pointer="manual")
    assert p.search("technical summaries")
    block = p.resonate("Please make this a concise technical summary.")
    assert "[MEMORANT_RESONANCE]" in block and "internal_only=true" in block

def test_content_hash_dedup(tmp_path: Path):
    p = MemoryPalace(tmp_path / "p.db"); a = p.add_claim("Same fact", source_pointer="a"); b = p.add_claim(" same   fact ", source_pointer="b"); assert a == b

def test_temporal_filter(tmp_path: Path):
    p = MemoryPalace(tmp_path / "p.db"); p.add_claim("Temporal fact", source_pointer="manual", valid_from="2026-01-01T00:00:00+00:00")
    assert not p.search("Temporal", as_of="2025-01-01T00:00:00+00:00"); assert p.search("Temporal", as_of="2026-06-01T00:00:00+00:00")

def test_invalidate_fact_cascades(tmp_path: Path):
    p = MemoryPalace(tmp_path / "p.db"); p.add_claim("Derived claim", source_pointer="fact:f1#seg0", fact_refs=["f1"])
    assert p.invalidate_claims_for_fact("f1") == 1; assert not p.search("Derived")

def test_digest_promote_reject(tmp_path: Path):
    p = MemoryPalace(tmp_path / "p.db"); p.add_claim("Digest claim", source_pointer="manual"); did = p.create_digest(version="v-test")
    assert p.list_digests(); state = p.promote_digest(did, tmp_path / "standing_state.md"); assert state.exists()
    did2 = p.create_digest(version="v-test-2"); p.reject_digest(did2, "bad diff"); assert "REJECTED: bad diff" in p.get_digest(did2)["diff_from_prior"]

def test_resonance_leak_guard(tmp_path: Path):
    p = MemoryPalace(tmp_path / "p.db"); p.add_claim("SQL debug traceback token=abc embedding vector", source_pointer="manual")
    block = p.resonate("SQL debug traceback token embedding")
    assert "token=abc" not in block and "embedding vector" not in block
