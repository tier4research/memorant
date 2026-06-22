"""Context Tuner v1 — adaptive context compression for LLM agent conversations.

Primary API: ContextTuner

Compresses conversation messages to fit within token limits while
preserving critical information. Features:
- Token counting and threshold-based auto-trigger
- Message chunking and summarization with key fact extraction
- Recovery/rollback via SQLite + FTS5 store
- Pluggable summarizer (default: truncation + key facts)
- Doctor contract health checks
- Steward-based migration management
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from memorant._vendor.steward import Steward
from memorant._vendor.doctor import DoctorReport, CheckResult, run_check, doctor_main

from .compressor import (
    compress_messages,
    count_message_tokens,
    chunk_messages,
    Summarizer,
)
from .recovery import RecoveryStore, RecoveryRecord
from .schema import SCHEMA_V1, MIGRATIONS


# ── Constants ──────────────────────────────────────────────────

COMPONENT_VERSION = "1.0.0"
DEFAULT_MAX_TOKENS = 8000
DEFAULT_COMPRESSION_RATIO = 0.5
DEFAULT_KEEP_LAST_N = 3


# ── Data types ─────────────────────────────────────────────────

@dataclass
class CompressedMessages:
    """Result of a compress() operation."""

    messages: list[dict[str, Any]]
    recovery_id: str
    original_tokens: int
    compressed_tokens: int
    compression_ratio: float


@dataclass
class TunerConfig:
    """ContextTuner configuration.

    Attributes:
        db_path: Path to the recovery SQLite database
        max_tokens: Token threshold; compression triggers above this
        compression_ratio: Target ratio of compressed to original tokens
        keep_last_n: Number of most recent messages to keep intact
        summarizer: Pluggable summarizer function (default: truncation + facts)
        encryption_key: Optional SQLCipher encryption key
    """

    db_path: str | Path = "./context_tuner.db"
    max_tokens: int = DEFAULT_MAX_TOKENS
    compression_ratio: float = DEFAULT_COMPRESSION_RATIO
    keep_last_n: int = DEFAULT_KEEP_LAST_N
    summarizer: Summarizer | None = None
    encryption_key: str | None = None


# ── ContextTuner (v1 primary API) ──────────────────────────────

class ContextTuner:
    """Primary API for Context Tuner v1.

    Adaptive context compression for LLM agent conversations.
    When a conversation grows too large for the context window,
    intelligently summarizes older messages while preserving
    critical information.

    Usage:
        tuner = ContextTuner("./tuner.db")
        result = tuner.compress(messages)
        if result.compression_ratio < 1.0:
            # Messages were compressed
            ...
        # Later, recover originals:
        original = tuner.decompress(result.recovery_id)
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        config: TunerConfig | None = None,
    ):
        self.config = config or TunerConfig()
        if db_path is not None:
            self.config.db_path = db_path
        self.db_path = Path(self.config.db_path)

        # Internal recovery store
        self._recovery = RecoveryStore(
            self.db_path,
            encryption_key=self.config.encryption_key,
        )
        # Steward alias (exposed for consistency with memorant pattern)
        self._steward = self._recovery._steward

    # ── Connection management ────────────────────────────────

    def connect(self) -> sqlite3.Connection:
        """Open a database connection to the recovery store.

        Uses WAL mode, foreign keys, and busy timeout.
        Supports optional SQLCipher encryption.
        """
        return self._recovery.connect()

    # ── Initialization & migration ───────────────────────────

    def init(self) -> list[str]:
        """Initialize database schema and run migrations.

        Idempotent — safe to call multiple times.
        Returns list of table names created.
        """
        return self._recovery.init()

    # ── Compression ──────────────────────────────────────────

    def compress(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int | None = None,
        compression_ratio: float | None = None,
        keep_last_n: int | None = None,
        session_metadata: dict[str, Any] | None = None,
    ) -> CompressedMessages:
        """Compress conversation messages to fit within token limits.

        If total tokens are under max_tokens, messages are returned
        unchanged (compression_ratio = 1.0). Otherwise, older messages
        are summarized while preserving system messages and recent turns.

        A recovery record is always saved to enable rollback via decompress().

        Args:
            messages: List of message dicts (with "role" and "content" keys)
            max_tokens: Override config token threshold
            compression_ratio: Override config compression ratio
            keep_last_n: Override config for messages to keep intact
            session_metadata: Optional metadata to store with the recovery record

        Returns:
            CompressedMessages with the (possibly compressed) message list
            and a recovery_id for later decompression.
        """
        self.init()

        max_tokens = max_tokens if max_tokens is not None else self.config.max_tokens
        compression_ratio = (
            compression_ratio if compression_ratio is not None
            else self.config.compression_ratio
        )
        keep_last_n = (
            keep_last_n if keep_last_n is not None else self.config.keep_last_n
        )

        # Run compression
        compressed, original_tokens, compressed_tokens = compress_messages(
            messages,
            max_tokens=max_tokens,
            compression_ratio=compression_ratio,
            keep_last_n=keep_last_n,
            summarizer=self.config.summarizer,
        )

        actual_ratio = compressed_tokens / max(1, original_tokens)

        # Save recovery record
        recovery_id = self._recovery.save(
            original_messages=messages,
            compressed_messages=compressed,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            compression_ratio=actual_ratio,
            session_metadata=session_metadata,
        )

        return CompressedMessages(
            messages=compressed,
            recovery_id=recovery_id,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            compression_ratio=actual_ratio,
        )

    # ── Decompression / rollback ─────────────────────────────

    def decompress(self, recovery_id: str) -> list[dict[str, Any]] | None:
        """Recover original messages from a compression session.

        Returns the original (pre-compression) message list,
        or None if the recovery_id is not found.

        Args:
            recovery_id: The recovery ID returned by compress()

        Returns:
            Original message list, or None if not found
        """
        record = self._recovery.load(recovery_id)
        if record is None:
            return None
        return record.original_messages

    def get_recovery_record(self, recovery_id: str) -> RecoveryRecord | None:
        """Get full recovery record including metadata."""
        return self._recovery.load(recovery_id)

    def list_recoveries(self, limit: int = 20) -> list[RecoveryRecord]:
        """List recent recovery sessions."""
        return self._recovery.list_recent(limit=limit)

    def delete_recovery(self, recovery_id: str) -> bool:
        """Delete a recovery record. Returns True if deleted."""
        return self._recovery.delete(recovery_id)

    def search_recoveries(self, query: str, limit: int = 10) -> list[RecoveryRecord]:
        """Search recovery sessions by compressed content via FTS5."""
        return self._recovery.search(query, limit=limit)

    # ── Token counting ───────────────────────────────────────

    def count_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Count approximate tokens across all messages."""
        return count_message_tokens(messages)

    def needs_compression(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None,
    ) -> bool:
        """Check if messages exceed the token threshold."""
        threshold = max_tokens if max_tokens is not None else self.config.max_tokens
        return count_message_tokens(messages) > threshold

    # ── Integrity & backup ───────────────────────────────────

    def integrity_check(self) -> bool:
        """Run PRAGMA integrity_check on the recovery database."""
        return self._recovery.integrity_check()

    def backup(self) -> Path:
        """Create a timestamped backup of the recovery database."""
        return self._recovery.backup()

    def migrate(self) -> int:
        """Run pending steward migrations."""
        return self._recovery.migrate()

    # ── Doctor ────────────────────────────────────────────────

    def doctor(self, json_output: bool = False) -> int:
        """Run health checks per the Agent Integrity doctor contract.

        Checks:
        - Database connection
        - Database integrity
        - Migration status

        Returns exit code: 0=healthy, 1=degraded, 2=unhealthy
        """
        checks = [
            run_check("database_connection", lambda: (True, "connected")),
            run_check(
                "database_integrity",
                lambda: (
                    self.integrity_check(),
                    "ok" if self.integrity_check() else "corrupt",
                ),
                degraded_on_error=False,
            ),
            run_check(
                "migration_status",
                lambda: (
                    self._steward.check_migration_complete(),
                    f"version {self._steward.user_version}",
                ),
            ),
        ]
        return doctor_main("context_tuner", COMPONENT_VERSION, checks, json_output=json_output)

    # ── Stats ────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return Context Tuner statistics.

        Includes recovery store stats and configuration summary.
        """
        recovery_stats = self._recovery.stats()
        return {
            **recovery_stats,
            "config": {
                "max_tokens": self.config.max_tokens,
                "compression_ratio": self.config.compression_ratio,
                "keep_last_n": self.config.keep_last_n,
            },
        }
