"""Tests for Context Tuner v1 — context compression with recovery."""

import json
import logging
import sqlite3
from pathlib import Path

import pytest

from context_tuner import (
    ContextTuner,
    TunerConfig,
    CompressedMessages,
    RecoveryStore,
    RecoveryRecord,
    RecoveryCorruptionError,
    CompressionOutcome,
    compress_messages,
    compress_messages_detailed,
    count_tokens,
    count_message_tokens,
    extract_key_facts,
    chunk_messages,
    default_summarize_chunk,
)
from context_tuner.compressor import _extract_text, _validate_compression_params


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


# ── _extract_text helper ───────────────────────────────────

class TestExtractText:
    def test_plain_string(self):
        assert _extract_text("hello world") == "hello world"

    def test_empty_string(self):
        assert _extract_text("") == ""

    def test_text_parts(self):
        content = [
            {"type": "text", "text": "Hello"},
            {"type": "text", "text": "World"},
        ]
        assert _extract_text(content) == "Hello World"

    def test_image_parts(self):
        content = [
            {"type": "text", "text": "See this:"},
            {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
        ]
        assert _extract_text(content) == "See this: [image]"

    def test_audio_parts(self):
        content = [
            {"type": "audio", "audio": "base64data"},
            {"type": "text", "text": "Transcribed."},
        ]
        assert _extract_text(content) == "[audio] Transcribed."

    def test_mixed_multimodal(self):
        content = [
            {"type": "text", "text": "Analysis:"},
            {"type": "image_url", "image_url": {"url": "img.png"}},
            {"type": "audio", "audio": "data"},
            {"type": "text", "text": "Done."},
        ]
        result = _extract_text(content)
        assert "Analysis:" in result
        assert "[image]" in result
        assert "[audio]" in result
        assert "Done." in result

    def test_non_dict_items_in_list(self):
        content = ["not a dict", {"type": "text", "text": "ok"}]
        assert _extract_text(content) == "ok"

    def test_unknown_type(self):
        content = [{"type": "video", "data": "x"}]
        assert _extract_text(content) == ""

    def test_non_string_non_list(self):
        assert _extract_text(42) == "42"
        assert _extract_text(None) == "None"


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

    def test_count_message_tokens_multimodal(self):
        """Multimodal messages should include image/audio token costs."""
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this"},
                    {"type": "image_url", "image_url": {"url": "img.png"}},
                ],
            },
        ]
        tokens = count_message_tokens(msgs)
        # Should include text + 85 (default image cost) + 4 (overhead)
        text_tokens = count_tokens("Describe this")
        assert tokens >= text_tokens + 85 + 4

    def test_count_message_tokens_custom_image_cost(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "img.png"}},
                ],
            },
        ]
        tokens = count_message_tokens(msgs, image_token_cost=200)
        assert tokens >= 200 + 4

    def test_count_message_tokens_custom_audio_cost(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "audio", "audio": "data"},
                ],
            },
        ]
        tokens = count_message_tokens(msgs, audio_token_cost=100)
        assert tokens >= 100 + 4

    def test_count_message_tokens_non_dict(self):
        """Non-dict messages should be handled gracefully."""
        msgs = ["not a dict", {"role": "user", "content": "hello"}]
        tokens = count_message_tokens(msgs)
        assert tokens > 0


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

    def test_summarize_multimodal_content(self):
        """Summarizer should handle list-valued content via _extract_text."""
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "DECISION: Use PostgreSQL for data storage."},
                    {"type": "image_url", "image_url": {"url": "schema.png"}},
                ],
            },
        ]
        result = default_summarize_chunk(msgs, max_chars=500)
        assert "PostgreSQL" in result
        assert "[image]" in result


# ── Compression Pipeline (deprecated wrapper) ───────────────

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
            msgs, max_tokens=300, keep_last_n=2
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
            msgs, max_tokens=300, keep_last_n=2
        )
        # System message must be preserved
        system_msgs = [m for m in compressed if m.get("role") == "system"]
        assert len(system_msgs) >= 1
        assert "CRITICAL" in system_msgs[0]["content"]

    def test_recent_messages_preserved(self):
        msgs = make_conversation(num_turns=10, verbose=True)
        compressed, _, _ = compress_messages(
            msgs, max_tokens=300, keep_last_n=4
        )
        # Check that the last user message is in the output
        # The last message should be from the most recent turn
        last_content = msgs[-1]["content"][:20]
        assert any(last_content in m.get("content", "") for m in compressed)

    def test_compressed_message_marked(self):
        msgs = make_conversation(num_turns=10, verbose=True)
        compressed, _, _ = compress_messages(
            msgs, max_tokens=300, keep_last_n=2
        )
        # Should have [COMPRESSED] markers (use higher budget so protected fits)
        compressed_marks = [m for m in compressed if "[COMPRESSED" in m.get("content", "")]
        assert len(compressed_marks) > 0

    def test_empty_messages(self):
        compressed, orig, comp = compress_messages([], max_tokens=100)
        assert compressed == []
        assert orig == 0
        assert comp == 0


# ── compress_messages_detailed (CompressionOutcome) ─────────

class TestCompressDetailed:
    def test_under_budget_returns_original(self):
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        outcome = compress_messages_detailed(msgs, max_tokens=10000)
        assert outcome.messages == msgs
        assert outcome.original_tokens == outcome.compressed_tokens
        assert outcome.within_budget is True
        assert outcome.budget_enforced is False
        assert outcome.degradation_reason is None

    def test_compression_with_budget_flag(self):
        msgs = make_conversation(num_turns=10, verbose=True)
        outcome = compress_messages_detailed(msgs, max_tokens=300, keep_last_n=2)
        assert outcome.compressed_tokens < outcome.original_tokens
        assert isinstance(outcome, CompressionOutcome)

    def test_protected_content_exceeds_budget(self):
        """When protected messages alone exceed max_tokens."""
        msgs = [
            {"role": "system", "content": "You are a helpful assistant. " * 50},
            {"role": "user", "content": "Hello " * 50},
            {"role": "assistant", "content": "Hi! " * 50},
        ]
        outcome = compress_messages_detailed(msgs, max_tokens=5, keep_last_n=1)
        assert outcome.within_budget is False
        assert outcome.budget_enforced is True
        assert outcome.degradation_reason == "protected_content_exceeds_budget"

    def test_indexed_reassembly_preserves_system_position(self):
        """System messages should stay at their original index."""
        msgs = [
            {"role": "user", "content": "Q1: " + "x " * 50},
            {"role": "system", "content": "Mid-system prompt"},
            {"role": "assistant", "content": "A1: " + "y " * 50},
            {"role": "user", "content": "Q2: " + "z " * 50},
            {"role": "assistant", "content": "A2: " + "w " * 50},
        ]
        outcome = compress_messages_detailed(msgs, max_tokens=100, keep_last_n=0)
        # The system message should be preserved
        system_msgs = [m for m in outcome.messages if m.get("role") == "system"]
        assert len(system_msgs) == 1
        assert system_msgs[0]["content"] == "Mid-system prompt"

    def test_keep_last_n_zero_compresses_all_non_system(self):
        """keep_last_n=0 should make all non-system messages eligible for compression."""
        msgs = [
            {"role": "system", "content": "System prompt."},
        ]
        for i in range(10):
            msgs.append({"role": "user", "content": f"User query {i}: " + "details " * 20})
            msgs.append({"role": "assistant", "content": f"Answer {i}: " + "explanation " * 20})

        outcome = compress_messages_detailed(msgs, max_tokens=500, keep_last_n=0)
        assert outcome.compressed_tokens < outcome.original_tokens
        # System should still be present
        assert any(m.get("role") == "system" for m in outcome.messages)

    def test_many_chunks_share_one_budget(self):
        """Many alternating-role chunks should share one global budget."""
        msgs = []
        for i in range(20):
            msgs.append({"role": "user", "content": f"Message {i}: " + "word " * 10})
            msgs.append({"role": "assistant", "content": f"Reply {i}: " + "word " * 10})

        outcome = compress_messages_detailed(msgs, max_tokens=200, keep_last_n=2)
        # Output should be smaller than input
        assert outcome.compressed_tokens <= outcome.original_tokens

    def test_multimodal_messages_compress(self):
        """Multimodal messages should compress without crashing."""
        msgs = [
            {"role": "system", "content": "You are a vision assistant."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is in this image? " * 10},
                    {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
                ],
            },
            {"role": "assistant", "content": "I see a cat. " * 20},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe the audio. " * 10},
                    {"type": "audio", "audio": "base64data"},
                ],
            },
            {"role": "assistant", "content": "I hear music. " * 20},
        ]
        outcome = compress_messages_detailed(msgs, max_tokens=100, keep_last_n=0)
        # Should not crash and should produce a result
        assert outcome.original_tokens > 0
        assert len(outcome.messages) > 0

    def test_custom_image_audio_costs_affect_compression(self):
        """Configured image/audio costs should affect token counting."""
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look at this"},
                    {"type": "image_url", "image_url": {"url": "img.png"}},
                ],
            },
        ]
        low_cost = count_message_tokens(msgs, image_token_cost=10)
        high_cost = count_message_tokens(msgs, image_token_cost=500)
        assert high_cost > low_cost

    def test_legacy_one_argument_summarizer_remains_compatible(self):
        """The v1 summarizer(messages) contract must continue to work."""
        msgs = [{"role": "user", "content": "word " * 200}]

        def legacy_summarizer(messages):
            return "legacy summary"

        outcome = compress_messages_detailed(
            msgs, max_tokens=30, keep_last_n=0, summarizer=legacy_summarizer
        )
        assert "legacy summary" in outcome.messages[0]["content"]


# ── Input Validation ────────────────────────────────────────

class TestInputValidation:
    def test_max_tokens_zero_raises(self):
        with pytest.raises(ValueError, match="max_tokens must be > 0"):
            _validate_compression_params(max_tokens=0, keep_last_n=3, compression_ratio=0.5)

    def test_max_tokens_negative_raises(self):
        with pytest.raises(ValueError, match="max_tokens must be > 0"):
            _validate_compression_params(max_tokens=-1, keep_last_n=3, compression_ratio=0.5)

    def test_keep_last_n_negative_raises(self):
        with pytest.raises(ValueError, match="keep_last_n must be >= 0"):
            _validate_compression_params(max_tokens=100, keep_last_n=-1, compression_ratio=0.5)

    def test_compression_ratio_below_zero_raises(self):
        with pytest.raises(ValueError, match="compression_ratio must be between"):
            _validate_compression_params(max_tokens=100, keep_last_n=3, compression_ratio=-0.1)

    def test_compression_ratio_above_one_raises(self):
        with pytest.raises(ValueError, match="compression_ratio must be between"):
            _validate_compression_params(max_tokens=100, keep_last_n=3, compression_ratio=1.5)

    def test_valid_params_pass(self):
        # Should not raise
        _validate_compression_params(max_tokens=100, keep_last_n=0, compression_ratio=0.0)
        _validate_compression_params(max_tokens=100, keep_last_n=10, compression_ratio=1.0)

    def test_detailed_validates_params(self):
        with pytest.raises(ValueError, match="max_tokens must be > 0"):
            compress_messages_detailed([], max_tokens=0)


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

    def test_save_multimodal_fts_content(self, recovery_store):
        """Multimodal compressed messages should save and be searchable."""
        rid = recovery_store.save(
            original_messages=[{"role": "user", "content": "Original"}],
            compressed_messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "searchable deploy keyword"},
                        {"type": "image_url", "image_url": {"url": "img.png"}},
                    ],
                }
            ],
        )
        results = recovery_store.search("deploy")
        assert len(results) >= 1


# ── RecoveryCorruptionError ─────────────────────────────────

class TestRecoveryCorruptionError:
    def test_error_attributes(self):
        err = RecoveryCorruptionError("abc-123", "original_messages", ValueError("bad json"))
        assert err.recovery_id == "abc-123"
        assert err.field == "original_messages"
        assert isinstance(err.original_error, ValueError)
        assert "abc-123" in str(err)
        assert "original_messages" in str(err)

    def test_error_without_original(self):
        err = RecoveryCorruptionError("id-1", "compressed_messages")
        assert err.original_error is None
        assert "id-1" in str(err)

    def test_load_raises_on_corrupt_original(self, recovery_store, tmp_path):
        """load() should raise RecoveryCorruptionError for corrupt data."""
        recovery_store.init()
        # Manually insert a corrupt record
        rid = "corrupt-test-1"
        with recovery_store.connect() as db:
            db.execute(
                """INSERT INTO recovery_sessions
                   (id, original_messages, compressed_messages)
                   VALUES (?, ?, ?)""",
                (rid, "NOT VALID JSON", '[{"role":"user","content":"ok"}]'),
            )
            db.execute(
                "INSERT INTO recovery_sessions_fts (id, searchable_content) VALUES (?, ?)",
                (rid, "ok"),
            )
            db.commit()

        with pytest.raises(RecoveryCorruptionError) as exc_info:
            recovery_store.load(rid)
        assert exc_info.value.recovery_id == rid
        assert exc_info.value.field == "original_messages"

    def test_load_raises_on_corrupt_compressed(self, recovery_store):
        """load() should raise RecoveryCorruptionError for corrupt compressed field."""
        recovery_store.init()
        rid = "corrupt-test-2"
        with recovery_store.connect() as db:
            db.execute(
                """INSERT INTO recovery_sessions
                   (id, original_messages, compressed_messages)
                   VALUES (?, ?, ?)""",
                (rid, '[{"role":"user","content":"ok"}]', "CORRUPT"),
            )
            db.execute(
                "INSERT INTO recovery_sessions_fts (id, searchable_content) VALUES (?, ?)",
                (rid, "ok"),
            )
            db.commit()

        with pytest.raises(RecoveryCorruptionError) as exc_info:
            recovery_store.load(rid)
        assert exc_info.value.field == "compressed_messages"

    def test_load_raises_on_non_list_data(self, recovery_store):
        """load() should raise when stored data is valid JSON but not a list."""
        recovery_store.init()
        rid = "corrupt-test-3"
        with recovery_store.connect() as db:
            db.execute(
                """INSERT INTO recovery_sessions
                   (id, original_messages, compressed_messages)
                   VALUES (?, ?, ?)""",
                (rid, '{"not": "a list"}', '[{"role":"user","content":"ok"}]'),
            )
            db.execute(
                "INSERT INTO recovery_sessions_fts (id, searchable_content) VALUES (?, ?)",
                (rid, "ok"),
            )
            db.commit()

        with pytest.raises(RecoveryCorruptionError) as exc_info:
            recovery_store.load(rid)
        assert "expected list" in str(exc_info.value.original_error).lower()

    def test_list_recent_skips_corrupt(self, recovery_store, caplog):
        """list_recent() should warn and skip corrupt rows."""
        recovery_store.save(
            original_messages=[{"role": "user", "content": "Good"}],
            compressed_messages=[{"role": "user", "content": "Good compressed"}],
        )
        # Manually insert a corrupt record
        recovery_store.init()
        with recovery_store.connect() as db:
            db.execute(
                """INSERT INTO recovery_sessions
                   (id, original_messages, compressed_messages)
                   VALUES (?, ?, ?)""",
                ("bad-id", "INVALID", '[{"role":"user","content":"ok"}]'),
            )
            db.execute(
                "INSERT INTO recovery_sessions_fts (id, searchable_content) VALUES (?, ?)",
                ("bad-id", "ok"),
            )
            db.commit()

        with caplog.at_level(logging.WARNING):
            records = recovery_store.list_recent()
        # Should return only the valid record
        assert len(records) == 1
        assert records[0].original_messages == [{"role": "user", "content": "Good"}]
        # Should have logged a warning
        assert any("corrupt" in r.message.lower() for r in caplog.records)

    def test_search_skips_corrupt(self, recovery_store, caplog):
        """search() should warn and skip corrupt rows."""
        recovery_store.save(
            original_messages=[{"role": "user", "content": "Deploy app"}],
            compressed_messages=[{"role": "user", "content": "deploy app compressed"}],
        )
        recovery_store.init()
        with recovery_store.connect() as db:
            db.execute(
                """INSERT INTO recovery_sessions
                   (id, original_messages, compressed_messages)
                   VALUES (?, ?, ?)""",
                ("bad-search-id", "NOT JSON", '[{"role":"user","content":"deploy stuff"}]'),
            )
            db.execute(
                "INSERT INTO recovery_sessions_fts (id, searchable_content) VALUES (?, ?)",
                ("bad-search-id", "deploy stuff"),
            )
            db.commit()

        with caplog.at_level(logging.WARNING):
            results = recovery_store.search("deploy")
        # Should return only the valid record
        assert len(results) == 1
        assert any("corrupt" in r.message.lower() for r in caplog.records)

    def test_load_missing_record_returns_none(self, recovery_store):
        """Missing records should return None, not raise."""
        assert recovery_store.load("totally-missing-id") is None


# ── Retention / Pruning ─────────────────────────────────────

class TestRetention:
    def test_defaults_do_not_prune(self, tmp_path):
        """Default config (None limits) should not prune anything."""
        store = RecoveryStore(tmp_path / "no_prune.db")
        for i in range(20):
            store.save(
                original_messages=[{"role": "user", "content": f"Msg {i}"}],
                compressed_messages=[{"role": "user", "content": f"M{i}"}],
            )
        assert len(store.list_recent(limit=100)) == 20

    def test_max_sessions_prunes(self, tmp_path):
        """max_sessions should limit stored sessions."""
        store = RecoveryStore(tmp_path / "prune_count.db", max_sessions=5)
        for i in range(10):
            store.save(
                original_messages=[{"role": "user", "content": f"Msg {i}"}],
                compressed_messages=[{"role": "user", "content": f"M{i}"}],
            )
        records = store.list_recent(limit=100)
        assert len(records) == 5

    def test_max_sessions_deterministic_ordering(self, tmp_path):
        """Pruning should keep the newest sessions by created_at, id."""
        store = RecoveryStore(tmp_path / "prune_order.db", max_sessions=3)
        # Use deterministic IDs so ordering by (created_at, id) is predictable
        custom_ids = [f"session-{i:03d}" for i in range(5)]
        for i, cid in enumerate(custom_ids):
            store.save(
                original_messages=[{"role": "user", "content": f"Msg {i}"}],
                compressed_messages=[{"role": "user", "content": f"M{i}"}],
                recovery_id=cid,
            )
        records = store.list_recent(limit=100)
        record_ids = {r.id for r in records}
        # All records share the same created_at (rapid inserts), so ordering
        # is by id ASC. Pruning removes oldest first (lowest ids), keeping
        # the 3 with highest ids: session-002, session-003, session-004.
        assert len(records) == 3
        assert "session-000" not in record_ids
        assert "session-001" not in record_ids
        assert "session-002" in record_ids
        assert "session-003" in record_ids
        assert "session-004" in record_ids

    def test_max_age_prunes(self, tmp_path):
        """max_age_days should remove old sessions."""
        store = RecoveryStore(tmp_path / "prune_age.db", max_age_days=30)
        store.init()
        # Insert a record with an old created_at
        with store.connect() as db:
            db.execute(
                """INSERT INTO recovery_sessions
                   (id, original_messages, compressed_messages, created_at)
                   VALUES (?, ?, ?, ?)""",
                ("old-session", "[]", "[]", "2020-01-01T00:00:00+00:00"),
            )
            db.execute(
                "INSERT INTO recovery_sessions_fts (id, searchable_content) VALUES (?, ?)",
                ("old-session", ""),
            )
            db.commit()

        # Save a fresh record
        store.save(
            original_messages=[{"role": "user", "content": "New"}],
            compressed_messages=[{"role": "user", "content": "New compressed"}],
        )
        # The old record should be pruned (either during save or via explicit prune)
        records = store.list_recent(limit=100)
        record_ids = {r.id for r in records}
        assert "old-session" not in record_ids

    def test_max_age_boundary_handles_sqlite_timestamp_format(self, tmp_path):
        """A row newer than the cutoff must survive despite timestamp format."""
        from datetime import datetime, timedelta, timezone

        store = RecoveryStore(tmp_path / "prune_age_boundary.db", max_age_days=1)
        recovery_id = store.save(
            original_messages=[{"role": "user", "content": "New"}],
            compressed_messages=[{"role": "user", "content": "New"}],
        )
        within_window = datetime.now(timezone.utc) - timedelta(hours=23)
        sqlite_timestamp = within_window.strftime("%Y-%m-%d %H:%M:%S")
        with store.connect() as db:
            db.execute(
                "UPDATE recovery_sessions SET created_at = ? WHERE id = ?",
                (sqlite_timestamp, recovery_id),
            )
            db.commit()
        store.prune()
        assert store.load(recovery_id) is not None

    def test_prune_returns_count(self, tmp_path):
        """prune() should return the number of sessions removed."""
        store = RecoveryStore(tmp_path / "prune_count_api.db", max_sessions=2)
        for i in range(5):
            store.save(
                original_messages=[{"role": "user", "content": f"Msg {i}"}],
                compressed_messages=[{"role": "user", "content": f"M{i}"}],
            )
        # After save, already pruned to 2. Explicit prune should remove 0 more.
        pruned = store.prune()
        assert pruned == 0
        assert len(store.list_recent(limit=100)) == 2

    def test_fts_deleted_atomically(self, tmp_path):
        """Pruning should delete FTS entries atomically with recovery rows."""
        store = RecoveryStore(tmp_path / "prune_fts.db", max_sessions=2)
        # Use deterministic IDs for predictable pruning
        for i in range(5):
            store.save(
                original_messages=[{"role": "user", "content": f"Msg {i}"}],
                compressed_messages=[{"role": "user", "content": f"searchable content {i}"}],
                recovery_id=f"fts-test-{i:03d}",
            )
        # FTS table should only have entries for the remaining 2 sessions
        with store.connect() as db:
            fts_count = db.execute(
                "SELECT COUNT(*) FROM recovery_sessions_fts"
            ).fetchone()[0]
            recovery_count = db.execute(
                "SELECT COUNT(*) FROM recovery_sessions"
            ).fetchone()[0]
        assert fts_count == recovery_count
        assert recovery_count == 2

    def test_config_max_sessions_on_tuner(self, tmp_path):
        """TunerConfig.max_sessions should propagate to RecoveryStore."""
        config = TunerConfig(
            db_path=tmp_path / "tuner_prune.db",
            max_sessions=3,
        )
        tuner = ContextTuner(config=config)
        assert tuner._recovery.max_sessions == 3

    def test_config_max_age_days_on_tuner(self, tmp_path):
        """TunerConfig.max_age_days should propagate to RecoveryStore."""
        config = TunerConfig(
            db_path=tmp_path / "tuner_age.db",
            max_age_days=7,
        )
        tuner = ContextTuner(config=config)
        assert tuner._recovery.max_age_days == 7

    def test_age_then_count_pruning_order(self, tmp_path):
        """Age pruning runs first, then count pruning."""
        store = RecoveryStore(
            tmp_path / "order.db", max_sessions=2, max_age_days=1
        )
        store.init()
        # Insert an old record
        with store.connect() as db:
            db.execute(
                """INSERT INTO recovery_sessions
                   (id, original_messages, compressed_messages, created_at)
                   VALUES (?, ?, ?, ?)""",
                ("ancient", "[]", "[]", "2020-01-01T00:00:00+00:00"),
            )
            db.execute(
                "INSERT INTO recovery_sessions_fts (id, searchable_content) VALUES (?, ?)",
                ("ancient", ""),
            )
            db.commit()

        # Save 3 fresh records with deterministic IDs
        store.save(
            original_messages=[{"role": "user", "content": "A"}],
            compressed_messages=[{"role": "user", "content": "A"}],
            recovery_id="fresh-aaa",
        )
        store.save(
            original_messages=[{"role": "user", "content": "B"}],
            compressed_messages=[{"role": "user", "content": "B"}],
            recovery_id="fresh-bbb",
        )
        store.save(
            original_messages=[{"role": "user", "content": "C"}],
            compressed_messages=[{"role": "user", "content": "C"}],
            recovery_id="fresh-ccc",
        )
        records = store.list_recent(limit=100)
        record_ids = {r.id for r in records}
        # Ancient should be gone (age limit), plus one more gone (count=2)
        # With same created_at, ordering by id ASC: fresh-aaa < fresh-bbb < fresh-ccc
        # Count limit removes oldest first: fresh-aaa goes, keeping fresh-bbb and fresh-ccc
        assert "ancient" not in record_ids
        assert len(records) == 2
        assert "fresh-aaa" not in record_ids
        assert "fresh-bbb" in record_ids
        assert "fresh-ccc" in record_ids


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
        result = tuner.compress(msgs, max_tokens=300, keep_last_n=2)
        assert result.compression_ratio < 1.0
        assert result.compressed_tokens < result.original_tokens
        assert result.recovery_id is not None

    def test_compress_round_trip(self, tuner):
        """Compress then decompress — should recover original messages."""
        msgs = make_conversation(num_turns=10, verbose=True)
        result = tuner.compress(msgs, max_tokens=300, keep_last_n=2)

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

    def test_compress_within_budget_on_tight_budget(self, tmp_path):
        """Protected content exceeding max_tokens should set within_budget=False."""
        config = TunerConfig(db_path=tmp_path / "tight.db", max_tokens=10)
        tuner = ContextTuner(config=config)
        msgs = [{"role": "system", "content": "You are helpful."}]
        for i in range(20):
            msgs.append({"role": "user", "content": f"Message {i}: " + "data " * 30})
            msgs.append({"role": "assistant", "content": f"Response {i}: " + "info " * 30})
        result = tuner.compress(msgs)
        assert result.within_budget is False
        assert result.compressed_tokens > 10


# ── TunerConfig ─────────────────────────────────────────────

class TestTunerConfig:
    def test_default_config(self):
        config = TunerConfig()
        assert config.max_tokens == 8000
        assert config.compression_ratio == 0.5
        assert config.keep_last_n == 3
        assert config.db_path == "./context_tuner.db"

    def test_default_multimodal_costs(self):
        config = TunerConfig()
        assert config.image_token_cost == 85
        assert config.audio_token_cost == 50

    def test_default_retention_none(self):
        config = TunerConfig()
        assert config.max_sessions is None
        assert config.max_age_days is None

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

    def test_custom_multimodal_costs(self, tmp_path):
        config = TunerConfig(
            db_path=tmp_path / "mm.db",
            image_token_cost=200,
            audio_token_cost=100,
        )
        tuner = ContextTuner(config=config)
        assert tuner.config.image_token_cost == 200
        assert tuner.config.audio_token_cost == 100

    def test_public_token_checks_use_custom_multimodal_costs(self, tmp_path):
        config = TunerConfig(
            db_path=tmp_path / "mm_checks.db",
            max_tokens=100,
            image_token_cost=1000,
        )
        tuner = ContextTuner(config=config)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "img.png"}}
                ],
            }
        ]
        assert tuner.count_tokens(messages) == 1005
        assert tuner.needs_compression(messages) is True

    def test_custom_retention(self, tmp_path):
        config = TunerConfig(
            db_path=tmp_path / "retention.db",
            max_sessions=50,
            max_age_days=7,
        )
        tuner = ContextTuner(config=config)
        assert tuner.config.max_sessions == 50
        assert tuner.config.max_age_days == 7

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

    def test_within_budget_default_true(self):
        result = CompressedMessages(
            messages=[], recovery_id="x",
            original_tokens=10, compressed_tokens=5, compression_ratio=0.5,
        )
        assert result.within_budget is True


# ── CompressionOutcome ──────────────────────────────────────

class TestCompressionOutcome:
    def test_dataclass_fields(self):
        outcome = CompressionOutcome(
            messages=[{"role": "user", "content": "Hi"}],
            original_tokens=100,
            compressed_tokens=50,
            within_budget=True,
            budget_enforced=False,
        )
        assert outcome.messages == [{"role": "user", "content": "Hi"}]
        assert outcome.original_tokens == 100
        assert outcome.compressed_tokens == 50
        assert outcome.within_budget is True
        assert outcome.budget_enforced is False
        assert outcome.degradation_reason is None

    def test_defaults(self):
        outcome = CompressionOutcome(
            messages=[], original_tokens=0, compressed_tokens=0,
        )
        assert outcome.within_budget is True
        assert outcome.budget_enforced is False
        assert outcome.degradation_reason is None


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


# ── Regression Tests — Audit 2026-06-22 (round 3) ──────────────

class TestInitEmptyDB:
    """Pre-created empty databases must initialize correctly."""

    def test_precreated_empty_db_initializes(self, tmp_path):
        """path.touch() then init() must work (not crash on migrations)."""
        from memorant import MemorantStore
        path = tmp_path / "empty.db"
        path.touch()
        store = MemorantStore(path)
        tables = store.init()
        assert "claim_units" in tables

    def test_fresh_db_initializes(self, tmp_path):
        """Brand new DB initializes from schema."""
        from memorant import MemorantStore
        store = MemorantStore(tmp_path / "fresh.db")
        tables = store.init()
        assert "claim_units" in tables


class TestMaxTokensEnforcement:
    """Compression must respect max_tokens budget."""

    def test_compression_within_budget_flag(self, tmp_path):
        """CompressedMessages.within_budget reports when budget exceeded."""
        from context_tuner import ContextTuner, TunerConfig

        # Generate messages that will compress but exceed a tight budget
        msgs = [{"role": "system", "content": "You are helpful."}]
        for i in range(20):
            msgs.append({"role": "user", "content": f"Message {i}: " + "data " * 30})
            msgs.append({"role": "assistant", "content": f"Response {i}: " + "info " * 30})

        config = TunerConfig(db_path=tmp_path / "tuner.db", max_tokens=10)
        tuner = ContextTuner(config=config)
        result = tuner.compress(msgs)

        # With max_tokens=10 and 20 verbose turns, budget should be exceeded
        assert result.within_budget is False
        assert result.compressed_tokens > 10

    def test_keep_last_n_zero(self, tmp_path):
        """keep_last_n=0 should compress all non-system messages."""
        from context_tuner import compress_messages_detailed

        msgs = [{"role": "system", "content": "System prompt."}]
        for i in range(10):
            msgs.append({"role": "user", "content": f"User query {i}: " + "details " * 20})
            msgs.append({"role": "assistant", "content": f"Answer {i}: " + "explanation " * 20})

        outcome = compress_messages_detailed(msgs, max_tokens=500, keep_last_n=0)
        # With keep_last_n=0, no recent messages kept → more compression
        # With verbose messages, compression should reduce tokens
        assert outcome.compressed_tokens < outcome.original_tokens


# ── Existing tuple-unpacking callers (compat wrapper) ────────

class TestCompatWrapper:
    """compress_messages() three-tuple wrapper must remain functional."""

    def test_tuple_unpacking(self):
        msgs = make_conversation(num_turns=5, verbose=True)
        compressed, orig, comp = compress_messages(msgs, max_tokens=300, keep_last_n=2)
        assert isinstance(compressed, list)
        assert isinstance(orig, int)
        assert isinstance(comp, int)

    def test_returns_same_as_detailed(self):
        """Wrapper should return the same data as detailed (minus metadata)."""
        msgs = make_conversation(num_turns=5, verbose=True)
        compressed, orig, comp = compress_messages(msgs, max_tokens=300, keep_last_n=2)
        outcome = compress_messages_detailed(msgs, max_tokens=300, keep_last_n=2)
        assert compressed == outcome.messages
        assert orig == outcome.original_tokens
        assert comp == outcome.compressed_tokens

    def test_deprecation_warning(self):
        """compress_messages() should emit a DeprecationWarning."""
        msgs = [{"role": "user", "content": "Hello"}]
        with pytest.warns(DeprecationWarning, match="deprecated"):
            compress_messages(msgs, max_tokens=10000)


# ── Edge Case Tests ─────────────────────────────────────────

class TestEdgeCases:
    """Edge cases for the indexed-segment compression pipeline."""

    def test_system_only_messages(self):
        """Only system messages — nothing to compress."""
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "system", "content": "Be concise."},
        ]
        outcome = compress_messages_detailed(msgs, max_tokens=10000)
        assert outcome.messages == msgs
        assert outcome.within_budget is True
        assert outcome.compressed_tokens == outcome.original_tokens

    def test_system_only_with_tight_budget(self):
        """System-only messages exceeding budget should return protected_content_exceeds_budget."""
        msgs = [
            {"role": "system", "content": "You are a helpful assistant. " * 100},
        ]
        outcome = compress_messages_detailed(msgs, max_tokens=5, keep_last_n=0)
        assert outcome.within_budget is False
        assert outcome.degradation_reason == "protected_content_exceeds_budget"
        assert len(outcome.messages) == 1
        assert outcome.messages[0]["role"] == "system"

    def test_all_messages_protected(self):
        """When keep_last_n >= non-system count, all messages are protected — no summaries."""
        msgs = [
            {"role": "system", "content": "System prompt."},
            {"role": "user", "content": "Hello there friend."},
            {"role": "assistant", "content": "Hi! How are you today?"},
        ]
        # keep_last_n=5 but only 2 non-system messages → all protected
        outcome = compress_messages_detailed(msgs, max_tokens=10000, keep_last_n=5)
        assert outcome.messages == msgs
        assert outcome.within_budget is True
        assert outcome.budget_enforced is False
        # No [COMPRESSED] markers
        for m in outcome.messages:
            assert "[COMPRESSED" not in m.get("content", "")

    def test_single_message(self):
        """Single non-system message — nothing to compress beyond itself."""
        msgs = [{"role": "user", "content": "Hello"}]
        outcome = compress_messages_detailed(msgs, max_tokens=10000)
        assert outcome.messages == msgs
        assert outcome.within_budget is True

    def test_single_message_exceeds_budget(self):
        """Single message exceeding budget should be protected_content_exceeds_budget."""
        msgs = [{"role": "user", "content": "Hello " * 200}]
        outcome = compress_messages_detailed(msgs, max_tokens=5, keep_last_n=1)
        # The single message is protected (keep_last_n=1), so protected > budget
        assert outcome.within_budget is False
        assert outcome.degradation_reason == "protected_content_exceeds_budget"

    def test_mid_conversation_system_preserves_position(self):
        """System message in the middle should stay in the middle after compression."""
        msgs = [
            {"role": "user", "content": "First question: " + "word " * 30},
            {"role": "assistant", "content": "First answer: " + "word " * 30},
            {"role": "system", "content": "Updated instructions: be more detailed."},
            {"role": "user", "content": "Second question: " + "word " * 30},
            {"role": "assistant", "content": "Second answer: " + "word " * 30},
        ]
        outcome = compress_messages_detailed(msgs, max_tokens=100, keep_last_n=0)
        # System message should be in the output
        system_msgs = [m for m in outcome.messages if m.get("role") == "system"]
        assert len(system_msgs) == 1
        assert system_msgs[0]["content"] == "Updated instructions: be more detailed."

    def test_non_contiguous_unprotected_ranges(self):
        """Multiple disjoint unprotected ranges should produce multiple summaries."""
        # system at 0, unprotected at 1-2, system at 3, unprotected at 4-5, protected at 6-7
        msgs = [
            {"role": "system", "content": "System prompt."},
            {"role": "user", "content": "Q1: " + "detail " * 30},
            {"role": "assistant", "content": "A1: " + "detail " * 30},
            {"role": "system", "content": "Mid-system update."},
            {"role": "user", "content": "Q2: " + "detail " * 30},
            {"role": "assistant", "content": "A2: " + "detail " * 30},
            {"role": "user", "content": "Q3: " + "detail " * 30},
            {"role": "assistant", "content": "A3: " + "detail " * 30},
        ]
        outcome = compress_messages_detailed(msgs, max_tokens=150, keep_last_n=2)
        # Should have compressed markers for the unprotected ranges
        compressed_marks = [m for m in outcome.messages if "[COMPRESSED" in m.get("content", "")]
        assert len(compressed_marks) >= 1
        # Both system messages should be preserved
        system_msgs = [m for m in outcome.messages if m.get("role") == "system"]
        assert len(system_msgs) == 2
        # Recent messages (Q3, A3) should be preserved
        assert any("Q3" in m.get("content", "") for m in outcome.messages)
        assert any("A3" in m.get("content", "") for m in outcome.messages)

    def test_budget_trimming_removes_oldest_summaries(self):
        """When over budget, oldest summaries should be removed first."""
        # Create messages with many small unprotected ranges
        msgs = [
            {"role": "system", "content": "System."},
        ]
        # Add 10 pairs of user/assistant, keep_last_n=2
        for i in range(10):
            msgs.append({"role": "user", "content": f"Q{i}: " + "word " * 20})
            msgs.append({"role": "assistant", "content": f"A{i}: " + "word " * 20})

        outcome = compress_messages_detailed(msgs, max_tokens=200, keep_last_n=2)
        # Should still produce a valid result
        assert len(outcome.messages) > 0
        # System should be present
        assert any(m.get("role") == "system" for m in outcome.messages)
        # Recent messages (Q9, A9) should be present
        assert any("Q9" in m.get("content", "") for m in outcome.messages)
        assert any("A9" in m.get("content", "") for m in outcome.messages)

    def test_exact_budget_boundary(self):
        """When compressed output equals max_tokens exactly, within_budget should be True."""
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        tokens = count_message_tokens(msgs)
        outcome = compress_messages_detailed(msgs, max_tokens=tokens)
        assert outcome.within_budget is True
        assert outcome.budget_enforced is False

    def test_zero_length_content_messages(self):
        """Messages with empty content should not cause errors."""
        msgs = [
            {"role": "system", "content": ""},
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "Actual content here."},
        ]
        outcome = compress_messages_detailed(msgs, max_tokens=10000)
        assert len(outcome.messages) == 3
        assert outcome.within_budget is True

    def test_compressed_output_preserves_order(self):
        """Compressed messages should maintain the original relative ordering."""
        msgs = [
            {"role": "system", "content": "System prompt."},
            {"role": "user", "content": "Q1: " + "word " * 30},
            {"role": "assistant", "content": "A1: " + "word " * 30},
            {"role": "user", "content": "Q2: " + "word " * 30},
            {"role": "assistant", "content": "A2: " + "word " * 30},
            {"role": "user", "content": "Q3: " + "word " * 30},
            {"role": "assistant", "content": "A3: " + "word " * 30},
        ]
        outcome = compress_messages_detailed(msgs, max_tokens=200, keep_last_n=2)
        # System should come first
        assert outcome.messages[0]["role"] == "system"
        # Last messages should be the protected recent ones
        assert "Q3" in outcome.messages[-2].get("content", "")
        assert "A3" in outcome.messages[-1].get("content", "")

    def test_multimodal_round_trip_through_tuner(self, tmp_path):
        """Multimodal messages should survive compress → save → decompress."""
        tuner = ContextTuner(tmp_path / "mm_tuner.db")
        msgs = [
            {"role": "system", "content": "Vision assistant."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What do you see?"},
                    {"type": "image_url", "image_url": {"url": "img.png"}},
                ],
            },
            {"role": "assistant", "content": "I see a cat."},
        ]
        result = tuner.compress(msgs, max_tokens=10000)
        recovered = tuner.decompress(result.recovery_id)
        assert recovered is not None
        assert len(recovered) == 3
        # Content should be preserved exactly
        assert recovered[1]["content"] == msgs[1]["content"]

    def test_output_never_exceeds_max_tokens_when_protected_fits(self):
        """When protected content fits in budget, output must not exceed max_tokens."""
        msgs = [
            {"role": "system", "content": "Short system."},
        ]
        for i in range(30):
            msgs.append({"role": "user", "content": f"Q{i}: " + "word " * 20})
            msgs.append({"role": "assistant", "content": f"A{i}: " + "word " * 20})

        max_tokens = 200
        outcome = compress_messages_detailed(msgs, max_tokens=max_tokens, keep_last_n=2)
        # Protected content (system + last 2) should fit in 200 tokens
        # So the output should be at or below max_tokens
        assert outcome.compressed_tokens <= max_tokens

    def test_multiple_system_messages_preserved(self):
        """All system messages should be preserved, not just the first one."""
        msgs = [
            {"role": "system", "content": "First system."},
            {"role": "user", "content": "Q1: " + "word " * 30},
            {"role": "assistant", "content": "A1: " + "word " * 30},
            {"role": "system", "content": "Second system."},
            {"role": "user", "content": "Q2: " + "word " * 30},
            {"role": "assistant", "content": "A2: " + "word " * 30},
        ]
        outcome = compress_messages_detailed(msgs, max_tokens=100, keep_last_n=0)
        system_contents = [
            m["content"] for m in outcome.messages if m.get("role") == "system"
        ]
        assert "First system." in system_contents
        assert "Second system." in system_contents


class TestCompressionDebug:
    def test_compress_debug_returns_diagnostics(self, tmp_path):
        tuner = ContextTuner(tmp_path / "debug.db")
        messages = [
            {"role": "system", "content": "Follow policy."},
            {"role": "user", "content": "old " * 200},
            {"role": "assistant", "content": "older " * 200},
            {"role": "user", "content": "recent question"},
        ]

        debug = tuner.compress_debug(messages, max_tokens=80, keep_last_n=1)

        assert debug.result.recovery_id
        assert debug.original_message_count == 4
        assert debug.compressed_message_count <= 4
        assert debug.protected_message_count == 2
        assert "system" in debug.preserved_roles
        assert tuner.decompress(debug.result.recovery_id) == messages



class TestCompressionBudgetRegression:
    def test_exact_protected_budget_drops_summaries(self):
        msgs = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "old " * 100},
        ]
        max_tokens = count_message_tokens([msgs[0]])
        outcome = compress_messages_detailed(msgs, max_tokens=max_tokens, keep_last_n=0)

        assert outcome.within_budget is True
        assert outcome.compressed_tokens <= max_tokens
        assert outcome.messages == [msgs[0]]

    def test_compression_ratio_affects_output_size(self):
        msgs = [{"role": "system", "content": "s"}]
        msgs.extend({"role": "user", "content": "word " * 100} for _ in range(5))

        aggressive = compress_messages_detailed(
            msgs, max_tokens=200, keep_last_n=0, compression_ratio=0.1
        )
        relaxed = compress_messages_detailed(
            msgs, max_tokens=200, keep_last_n=0, compression_ratio=0.9
        )

        assert aggressive.compressed_tokens < relaxed.compressed_tokens
        assert aggressive.compressed_tokens <= 200
        assert relaxed.compressed_tokens <= 200
