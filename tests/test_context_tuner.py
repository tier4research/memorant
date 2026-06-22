"""Tests for Context Tuner v1 — context compression with recovery."""

import json
import sqlite3
from pathlib import Path

import pytest

from context_tuner import (
    ContextTuner,
    TunerConfig,
    CompressedMessages,
    RecoveryStore,
    RecoveryRecord,
    compress_messages,
    count_tokens,
    count_message_tokens,
    extract_key_facts,
    chunk_messages,
    default_summarize_chunk,
)


# ── Sample messages ─────────────────────────────────────────

def make_conversation(num_turns: int = 20, verbose: bool = False) -> list[dict]:
    """Generate a synthetic conversation with num_turns exchanges.

    Each turn has a user message and an assistant response.
    Set verbose=True to generate longer messages that trigger compression.
    """
    messages: list[dict] = [
        {"role": "system", "content": "You are a helpful assistant."},
    ]
    for i in range(1, num_turns + 1):
        user_content = f"User message {i}: " + (
            f"This is a detailed query about topic number {i}. "
            f"It contains multiple sentences with specific information. "
            f"The user wants to know about various aspects of the subject. "
            f"Additional context and background details are provided for clarity. "
            if verbose else f"Question {i}?"
        )
        messages.append({"role": "user", "content": user_content})

        assistant_content = f"Assistant response {i}: " + (
            f"Here is a comprehensive answer to your query about topic {i}. "
            f"I'll break this down into several key points. First, the primary "
            f"consideration involves understanding the core concepts. Second, "
            f"we need to examine the practical implications. Third, there are "
            f"several important caveats to note. Finally, I recommend the "
            f"following course of action based on best practices."
            if verbose else f"Answer to question {i}."
        )
        messages.append({"role": "assistant", "content": assistant_content})

    return messages


# ── Fixtures ────────────────────────────────────────────────

@pytest.fixture
def tuner(tmp_path: Path) -> ContextTuner:
    return ContextTuner(tmp_path / "test_tuner.db")


@pytest.fixture
def recovery_store(tmp_path: Path) -> RecoveryStore:
    return RecoveryStore(tmp_path / "test_recovery.db")


# ── Token Counting ──────────────────────────────────────────

class TestTokenCounting:
    def test_count_tokens_empty(self):
        assert count_tokens("") == 0

    def test_count_tokens_short(self):
        # ~4 chars per token
        assert count_tokens("hello world") >= 2

    def test_count_tokens_longer(self):
        text = "This is a longer piece of text that should be multiple tokens."
        assert count_tokens(text) > 5

    def test_count_message_tokens(self):
        msgs = [
            {"role": "user", "content": "Hello there"},
            {"role": "assistant", "content": "Hi! How can I help?"},
        ]
        tokens = count_message_tokens(msgs)
        assert tokens > 0
        # Should include 4 tokens overhead per message
        assert tokens >= count_tokens("Hello there") + count_tokens("Hi! How can I help?") + 8


# ── Key Fact Extraction ─────────────────────────────────────

class TestKeyFactExtraction:
    def test_extract_from_simple_text(self):
        text = "The user wants to deploy on Fridays. IMPORTANT: backup before deploy."
        facts = extract_key_facts(text, max_facts=5)
        assert len(facts) > 0

    def test_extract_empty_text(self):
        assert extract_key_facts("") == []

    def test_extract_markers(self):
        text = "DECISION: Use PostgreSQL. ACTION: Update config. NOTE: This is critical."
        facts = extract_key_facts(text, max_facts=10)
        # Should find the marked sentences
        assert len(facts) >= 1

    def test_extract_deduplicates(self):
        text = "IMPORTANT: backup. IMPORTANT: backup. IMPORTANT: backup."
        facts = extract_key_facts(text, max_facts=10)
        # Should deduplicate
        assert len(facts) == 1

    def test_extract_urls(self):
        text = "See https://example.com/docs and https://test.com/api for details."
        facts = extract_key_facts(text, max_facts=10)
        urls = [f for f in facts if "http" in f]
        assert len(urls) >= 1

    def test_filters_stop_words_only(self):
        text = "The a an is are was were be been. IMPORTANT: real content here."
        facts = extract_key_facts(text, max_facts=5)
        # Should not include stop-word-only "facts"
        for f in facts:
            assert len(f) >= 4


# ── Message Chunking ────────────────────────────────────────

class TestMessageChunking:
    def test_chunk_splits_correctly(self):
        msgs = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
            {"role": "user", "content": "Q3"},
            {"role": "assistant", "content": "A3"},
        ]
        system, recent, old = chunk_messages(msgs, keep_last_n=2)
        assert len(system) == 1
        assert system[0]["role"] == "system"
        assert len(recent) == 2
        assert recent[0]["content"] == "Q3"
        assert recent[1]["content"] == "A3"
        assert len(old) == 4  # Q1, A1, Q2, A2

    def test_chunk_fewer_than_keep(self):
        msgs = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
        ]
        system, recent, old = chunk_messages(msgs, keep_last_n=5)
        assert len(system) == 0
        assert len(recent) == 2
        assert len(old) == 0

    def test_chunk_no_system(self):
        msgs = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
        ]
        system, recent, old = chunk_messages(msgs, keep_last_n=2)
        assert len(system) == 0
        assert len(recent) == 2
        assert len(old) == 2


# ── Default Summarizer ──────────────────────────────────────

class TestDefaultSummarizer:
    def test_summarize_empty(self):
        result = default_summarize_chunk([])
        assert result == ""

    def test_summarize_short(self):
        msgs = [{"role": "user", "content": "Brief message."}]
        result = default_summarize_chunk(msgs, max_chars=200)
        assert "Brief message" in result
        assert "[CONTENT]" in result

    def test_summarize_with_facts(self):
        msgs = [
            {"role": "user", "content": "DECISION: Use Redis for caching. The cache should have a TTL of 300s."},
            {"role": "assistant", "content": "I'll configure Redis with the specified TTL."},
        ]
        result = default_summarize_chunk(msgs, max_chars=500)
        assert "[KEY FACTS]" in result
        assert "[CONTENT]" in result

    def test_summarize_truncates_long(self):
        long_msg = [{"role": "user", "content": "A" * 1000}]
        result = default_summarize_chunk(long_msg, max_chars=100)
        assert len(result) < 500  # Should be significantly shorter than original
        assert "..." in result


# ── Compression Pipeline ────────────────────────────────────

class TestCompressMessages:
    def test_no_compression_needed(self):
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        compressed, orig_tokens, comp_tokens = compress_messages(
            msgs, max_tokens=10000
        )
        assert compressed == msgs
        assert orig_tokens == comp_tokens

    def test_compression_triggers(self):
        msgs = make_conversation(num_turns=10, verbose=True)
        compressed, orig_tokens, comp_tokens = compress_messages(
            msgs, max_tokens=50, keep_last_n=2
        )
        # Should have compressed
        assert comp_tokens < orig_tokens
        # Compressed output should still have system + recent messages
        assert any(m.get("role") == "system" for m in compressed)

    def test_system_preserved(self):
        msgs = [
            {"role": "system", "content": "CRITICAL: You must always be polite."},
        ] + make_conversation(num_turns=8, verbose=True)[1:]  # Skip system from helper
        compressed, _, _ = compress_messages(
            msgs, max_tokens=30, keep_last_n=2
        )
        # System message must be preserved
        system_msgs = [m for m in compressed if m.get("role") == "system"]
        assert len(system_msgs) >= 1
        assert "CRITICAL" in system_msgs[0]["content"]

    def test_recent_messages_preserved(self):
        msgs = make_conversation(num_turns=10, verbose=True)
        compressed, _, _ = compress_messages(
            msgs, max_tokens=30, keep_last_n=4
        )
        # Check that the last user message is in the output
        # The last message should be from the most recent turn
        last_content = msgs[-1]["content"][:20]
        assert any(last_content in m.get("content", "") for m in compressed)

    def test_compressed_message_marked(self):
        msgs = make_conversation(num_turns=10, verbose=True)
        compressed, _, _ = compress_messages(
            msgs, max_tokens=30, keep_last_n=2
        )
        # Should have [COMPRESSED] markers
        compressed_marks = [m for m in compressed if "[COMPRESSED" in m.get("content", "")]
        assert len(compressed_marks) > 0

    def test_empty_messages(self):
        compressed, orig, comp = compress_messages([], max_tokens=100)
        assert compressed == []
        assert orig == 0
        assert comp == 0


# ── RecoveryStore ───────────────────────────────────────────

class TestRecoveryStore:
    def test_init_creates_tables(self, recovery_store):
        tables = recovery_store.init()
        assert "recovery_sessions" in tables
        assert "recovery_sessions_fts" in tables

    def test_save_and_load(self, recovery_store):
        original = [{"role": "user", "content": "Hello"}]
        compressed = [{"role": "user", "content": "Hi"}]
        rid = recovery_store.save(
            original_messages=original,
            compressed_messages=compressed,
            original_tokens=10,
            compressed_tokens=5,
            compression_ratio=0.5,
        )
        assert rid is not None

        record = recovery_store.load(rid)
        assert record is not None
        assert record.original_messages == original
        assert record.compressed_messages == compressed
        assert record.original_tokens == 10
        assert record.compressed_tokens == 5
        assert record.compression_ratio == 0.5

    def test_load_nonexistent(self, recovery_store):
        assert recovery_store.load("nonexistent") is None

    def test_delete(self, recovery_store):
        original = [{"role": "user", "content": "Test"}]
        compressed = [{"role": "user", "content": "T"}]
        rid = recovery_store.save(
            original_messages=original,
            compressed_messages=compressed,
        )
        assert recovery_store.delete(rid)
        assert recovery_store.load(rid) is None

    def test_delete_nonexistent(self, recovery_store):
        assert not recovery_store.delete("nonexistent")

    def test_list_recent(self, recovery_store):
        for i in range(5):
            recovery_store.save(
                original_messages=[{"role": "user", "content": f"Msg {i}"}],
                compressed_messages=[{"role": "user", "content": f"M{i}"}],
            )
        records = recovery_store.list_recent(limit=3)
        assert len(records) == 3
        # Most recent first
        assert records[0].created_at >= records[-1].created_at

    def test_search(self, recovery_store):
        recovery_store.save(
            original_messages=[{"role": "user", "content": "Deploy on Friday"}],
            compressed_messages=[{"role": "user", "content": "deploy friday"}],
        )
        recovery_store.save(
            original_messages=[{"role": "user", "content": "Test the API"}],
            compressed_messages=[{"role": "user", "content": "test api"}],
        )
        results = recovery_store.search("deploy")
        assert len(results) >= 1
        assert any("deploy" in r.compressed_messages[0]["content"].lower() for r in results)

    def test_search_no_results(self, recovery_store):
        recovery_store.save(
            original_messages=[{"role": "user", "content": "Hello"}],
            compressed_messages=[{"role": "user", "content": "Hi"}],
        )
        results = recovery_store.search("xyznonexistent12345")
        assert len(results) == 0

    def test_integrity_check(self, recovery_store):
        recovery_store.init()
        assert recovery_store.integrity_check()

    def test_stats(self, recovery_store):
        recovery_store.save(
            original_messages=[{"role": "user", "content": "A"}],
            compressed_messages=[{"role": "user", "content": "B"}],
            original_tokens=100,
            compressed_tokens=50,
            compression_ratio=0.5,
        )
        stats = recovery_store.stats()
        assert stats["total_sessions"] == 1
        assert stats["total_original_tokens"] == 100
        assert stats["total_compressed_tokens"] == 50
        assert stats["avg_compression_ratio"] == 0.5

    def test_save_with_metadata(self, recovery_store):
        rid = recovery_store.save(
            original_messages=[{"role": "user", "content": "X"}],
            compressed_messages=[{"role": "user", "content": "Y"}],
            session_metadata={"session_id": "test-123", "agent": "hermes"},
        )
        record = recovery_store.load(rid)
        assert record.session_metadata == {"session_id": "test-123", "agent": "hermes"}

    def test_save_with_custom_id(self, recovery_store):
        rid = recovery_store.save(
            original_messages=[{"role": "user", "content": "X"}],
            compressed_messages=[{"role": "user", "content": "Y"}],
            recovery_id="my-custom-id",
        )
        assert rid == "my-custom-id"
        assert recovery_store.load("my-custom-id") is not None


# ── ContextTuner (primary API) ──────────────────────────────

class TestContextTuner:
    def test_init_creates_db(self, tuner):
        tables = tuner.init()
        assert "recovery_sessions" in tables

    def test_connect(self, tuner):
        conn = tuner.connect()
        assert isinstance(conn, sqlite3.Connection)
        conn.close()

    def test_compress_no_threshold(self, tuner):
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        result = tuner.compress(msgs, max_tokens=10000)
        assert result.messages == msgs
        assert result.compression_ratio == 1.0
        assert result.original_tokens == result.compressed_tokens
        assert result.recovery_id is not None

    def test_compress_with_compression(self, tuner):
        msgs = make_conversation(num_turns=15, verbose=True)
        result = tuner.compress(msgs, max_tokens=50, keep_last_n=2)
        assert result.compression_ratio < 1.0
        assert result.compressed_tokens < result.original_tokens
        assert result.recovery_id is not None

    def test_compress_round_trip(self, tuner):
        """Compress then decompress — should recover original messages."""
        msgs = make_conversation(num_turns=10, verbose=True)
        result = tuner.compress(msgs, max_tokens=50, keep_last_n=2)

        # Decompress and verify original messages are recovered
        original = tuner.decompress(result.recovery_id)
        assert original is not None
        assert len(original) == len(msgs)
        assert original == msgs

    def test_compress_round_trip_no_compression(self, tuner):
        """Even without compression, recovery should work."""
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        result = tuner.compress(msgs, max_tokens=10000)
        original = tuner.decompress(result.recovery_id)
        assert original == msgs

    def test_decompress_nonexistent(self, tuner):
        assert tuner.decompress("nonexistent-id") is None

    def test_get_recovery_record(self, tuner):
        msgs = make_conversation(num_turns=5)
        result = tuner.compress(msgs)
        record = tuner.get_recovery_record(result.recovery_id)
        assert record is not None
        assert record.original_messages == msgs
        assert record.compressed_messages == result.messages

    def test_list_recoveries(self, tuner):
        for i in range(3):
            msgs = [{"role": "user", "content": f"Message {i}"}]
            tuner.compress(msgs)
        records = tuner.list_recoveries(limit=10)
        assert len(records) >= 3

    def test_delete_recovery(self, tuner):
        msgs = [{"role": "user", "content": "Test"}]
        result = tuner.compress(msgs)
        assert tuner.delete_recovery(result.recovery_id)
        assert tuner.decompress(result.recovery_id) is None

    def test_search_recoveries(self, tuner):
        tuner.compress([{"role": "user", "content": "Deploy the application"}])
        tuner.compress([{"role": "user", "content": "Test the database"}])
        results = tuner.search_recoveries("deploy")
        assert len(results) >= 1

    def test_count_tokens(self, tuner):
        msgs = [{"role": "user", "content": "Hello world"}]
        tokens = tuner.count_tokens(msgs)
        assert tokens > 0

    def test_needs_compression(self, tuner):
        small = [{"role": "user", "content": "Hi"}]
        assert not tuner.needs_compression(small, max_tokens=10000)

        large = make_conversation(num_turns=20, verbose=True)
        assert tuner.needs_compression(large, max_tokens=10)

    def test_stats(self, tuner):
        msgs = make_conversation(num_turns=5)
        tuner.compress(msgs)
        stats = tuner.stats()
        assert stats["total_sessions"] >= 1
        assert "config" in stats
        assert stats["config"]["max_tokens"] == tuner.config.max_tokens

    def test_doctor(self, tuner):
        tuner.init()
        exit_code = tuner.doctor(json_output=False)
        assert exit_code == 0

    def test_doctor_json(self, tuner, capsys):
        tuner.init()
        exit_code = tuner.doctor(json_output=True)
        captured = capsys.readouterr()
        report = json.loads(captured.out)
        assert report["status"] == "healthy"
        assert report["component"] == "context_tuner"
        assert "checks" in report

    def test_integrity_check(self, tuner):
        tuner.init()
        assert tuner.integrity_check()

    def test_integrity_check_before_init(self, tuner):
        # Should work even before init (no DB yet)
        assert tuner.integrity_check()

    def test_session_metadata(self, tuner):
        msgs = [{"role": "user", "content": "Test"}]
        result = tuner.compress(
            msgs,
            session_metadata={"run_id": "run-001", "agent": "test-agent"},
        )
        record = tuner.get_recovery_record(result.recovery_id)
        assert record.session_metadata == {"run_id": "run-001", "agent": "test-agent"}


# ── TunerConfig ─────────────────────────────────────────────

class TestTunerConfig:
    def test_default_config(self):
        config = TunerConfig()
        assert config.max_tokens == 8000
        assert config.compression_ratio == 0.5
        assert config.keep_last_n == 3
        assert config.db_path == "./context_tuner.db"

    def test_custom_config(self, tmp_path):
        config = TunerConfig(
            db_path=tmp_path / "custom.db",
            max_tokens=4000,
            compression_ratio=0.3,
            keep_last_n=5,
        )
        tuner = ContextTuner(config=config)
        assert tuner.config.max_tokens == 4000
        assert tuner.config.compression_ratio == 0.3
        assert tuner.config.keep_last_n == 5

    def test_db_path_override(self, tmp_path):
        """db_path arg to constructor overrides config.db_path."""
        config = TunerConfig(db_path=tmp_path / "config.db")
        tuner = ContextTuner(db_path=tmp_path / "override.db", config=config)
        assert tuner.db_path == Path(tmp_path / "override.db")

    def test_db_path_in_config_only(self, tmp_path):
        config = TunerConfig(db_path=tmp_path / "config_only.db")
        tuner = ContextTuner(config=config)
        assert tuner.db_path == Path(tmp_path / "config_only.db")


# ── CompressedMessages ──────────────────────────────────────

class TestCompressedMessages:
    def test_dataclass_fields(self):
        result = CompressedMessages(
            messages=[{"role": "user", "content": "Hi"}],
            recovery_id="abc-123",
            original_tokens=100,
            compressed_tokens=50,
            compression_ratio=0.5,
        )
        assert result.messages == [{"role": "user", "content": "Hi"}]
        assert result.recovery_id == "abc-123"
        assert result.original_tokens == 100
        assert result.compressed_tokens == 50
        assert result.compression_ratio == 0.5


# ── RecoveryRecord ──────────────────────────────────────────

class TestRecoveryRecord:
    def test_dataclass_fields(self):
        record = RecoveryRecord(
            id="test-id",
            original_messages=[{"role": "user", "content": "Original"}],
            compressed_messages=[{"role": "user", "content": "Compressed"}],
            original_tokens=10,
            compressed_tokens=5,
            compression_ratio=0.5,
            created_at="2026-01-01T00:00:00",
        )
        assert record.id == "test-id"
        assert record.original_messages == [{"role": "user", "content": "Original"}]
        assert record.compressed_tokens == 5


# ── Multiple compression cycles ─────────────────────────────

class TestMultipleCompressions:
    def test_sequential_compressions(self, tuner):
        """Multiple compress/decompress cycles should work independently."""
        msgs1 = [{"role": "user", "content": "First conversation topic A"}]
        msgs2 = [{"role": "user", "content": "Second conversation topic B"}]

        result1 = tuner.compress(msgs1)
        result2 = tuner.compress(msgs2)

        assert result1.recovery_id != result2.recovery_id

        recovered1 = tuner.decompress(result1.recovery_id)
        recovered2 = tuner.decompress(result2.recovery_id)

        assert recovered1 == msgs1
        assert recovered2 == msgs2

    def test_same_content_different_sessions(self, tuner):
        """Same messages compressed twice should get different recovery IDs."""
        msgs = [{"role": "user", "content": "Repeat me"}]
        result1 = tuner.compress(msgs)
        result2 = tuner.compress(msgs)
        assert result1.recovery_id != result2.recovery_id

        # Both should recover to the same original
        assert tuner.decompress(result1.recovery_id) == msgs
        assert tuner.decompress(result2.recovery_id) == msgs


# ── Steward integration ─────────────────────────────────────

class TestStewardIntegration:
    def test_steward_accessible(self, tuner):
        tuner.init()
        assert tuner._steward.user_version >= 0

    def test_backup_creates_file(self, tuner):
        tuner.init()
        backup_path = tuner.backup()
        assert backup_path.exists()
        assert ".backup-" in backup_path.name

    def test_migrate_returns_version(self, tuner):
        tuner.init()
        version = tuner.migrate()
        assert version >= 0
