"""Recovery store for Context Tuner — SQLite + FTS5 backed rollback.

Stores original messages for each compression session so they can be
recovered (decompressed) later. Uses FTS5 for searching compressed content.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from memorant._vendor.steward import Steward

from .compressor import _extract_text
from .errors import RecoveryCorruptionError
from .schema import SCHEMA_V1, MIGRATIONS

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json_load(
    data: str | None,
    default: Any = None,
) -> Any:
    """Load JSON, returning default on any parse error.

    Isolates malformed rows so one corrupt record doesn't break
    list/search operations.
    """
    if not data:
        return default
    try:
        return json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return default


def _decode_message_list(
    raw: str | None,
    recovery_id: str,
    field: str,
) -> list[dict[str, Any]]:
    """Decode a JSON field as a message list, raising on corruption.

    Raises RecoveryCorruptionError if the field is present but malformed
    or does not decode to a list.
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RecoveryCorruptionError(recovery_id, field, exc)
    if not isinstance(parsed, list):
        raise RecoveryCorruptionError(
            recovery_id, field,
            TypeError(f"expected list, got {type(parsed).__name__}"),
        )
    return parsed


def _validate_message_list(
    raw: str | None,
    recovery_id: str,
    field: str,
) -> list[dict[str, Any]] | None:
    """Validate a field as a message list, returning None on corruption.

    Used by list_recent() and search() to skip corrupt rows with a warning
    rather than raising.
    """
    try:
        return _decode_message_list(raw, recovery_id, field)
    except RecoveryCorruptionError as exc:
        logger.warning("Skipping corrupt recovery record: %s", exc)
        return None


# ── Data types ───────────────────────────────────────────────────

@dataclass
class RecoveryRecord:
    """A stored recovery session with original and compressed messages."""

    id: str
    original_messages: list[dict[str, Any]]
    compressed_messages: list[dict[str, Any]]
    original_tokens: int
    compressed_tokens: int
    compression_ratio: float
    created_at: str
    session_metadata: dict[str, Any] | None = None


# ── RecoveryStore ────────────────────────────────────────────────

class RecoveryStore:
    """SQLite-backed recovery store for context compression rollback.

    Stores original messages alongside their compressed versions,
    indexed via FTS5 for searchability. Uses the Steward for
    migration management and integrity checks.

    Attributes:
        db_path: Path to the SQLite database.
        max_sessions: Optional cap on recovery sessions (None = unbounded).
        max_age_days: Optional max age in days for recovery sessions (None = unbounded).
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        busy_timeout_ms: int = 5000,
        encryption_key: str | None = None,
        max_sessions: int | None = None,
        max_age_days: int | None = None,
    ):
        self.db_path = Path(db_path)
        self._steward = Steward(
            self.db_path,
            busy_timeout_ms=busy_timeout_ms,
            encryption_key=encryption_key,
        )
        self._encryption_key = encryption_key
        self.max_sessions = max_sessions
        self.max_age_days = max_age_days

    # ── Connection management ────────────────────────────────

    def connect(self) -> sqlite3.Connection:
        """Open a database connection with WAL, foreign keys, busy timeout."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        if self._encryption_key:
            import sqlcipher3
            db = sqlcipher3.connect(str(self.db_path))
            db.execute(f"PRAGMA key = '{self._encryption_key}'")
        else:
            db = sqlite3.connect(str(self.db_path))

        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(f"PRAGMA busy_timeout={self._steward.busy_timeout_ms}")
        return db

    # ── Initialization & migration ───────────────────────────

    def init(self) -> list[str]:
        """Initialize database schema and run migrations.

        Detects empty vs legacy by table inspection (recovery_sessions table).
        """
        target = max(MIGRATIONS) if MIGRATIONS else 0

        is_legacy = False
        if self.db_path.exists():
            try:
                with self.connect() as db:
                    row = db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='recovery_sessions'"
                    ).fetchone()
                    is_legacy = row is not None
            except Exception:
                is_legacy = False

        if is_legacy:
            self._steward.initialize(target)
            for version, sql in sorted(MIGRATIONS.items()):
                self._steward.add_migration(version, sql)
            self._steward.migrate()
        else:
            with self.connect() as db:
                for sql in SCHEMA_V1.values():
                    db.execute(sql)
                db.execute(f"PRAGMA user_version = {target}")
                db.commit()
            self._steward.initialize(target)
            for version, sql in sorted(MIGRATIONS.items()):
                self._steward.add_migration(version, sql)

        return list(SCHEMA_V1)

    # ── CRUD ─────────────────────────────────────────────────

    def save(
        self,
        original_messages: list[dict[str, Any]],
        compressed_messages: list[dict[str, Any]],
        *,
        original_tokens: int = 0,
        compressed_tokens: int = 0,
        compression_ratio: float = 0.0,
        session_metadata: dict[str, Any] | None = None,
        recovery_id: str | None = None,
    ) -> str:
        """Save a recovery session. Returns the recovery ID.

        If recovery_id is not provided, a UUID is generated.
        Pruning (if configured) happens atomically in the same transaction.
        """
        self.init()
        rid = recovery_id or str(uuid.uuid4())

        original_json = json.dumps(original_messages, ensure_ascii=False)
        compressed_json = json.dumps(compressed_messages, ensure_ascii=False)

        # Build searchable content from compressed messages using _extract_text
        searchable = " ".join(
            _extract_text(m.get("content", ""))
            for m in compressed_messages
            if isinstance(m, dict) and m.get("content")
        )

        with self.connect() as db:
            db.execute(
                """INSERT INTO recovery_sessions (
                    id, original_messages, compressed_messages,
                    original_tokens, compressed_tokens, compression_ratio,
                    session_metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    rid,
                    original_json,
                    compressed_json,
                    original_tokens,
                    compressed_tokens,
                    compression_ratio,
                    json.dumps(session_metadata or {}),
                ),
            )
            db.execute(
                "INSERT INTO recovery_sessions_fts (id, searchable_content) VALUES (?, ?)",
                (rid, searchable),
            )

            # Atomic pruning in same transaction
            self._prune_impl(db)

            db.commit()

        return rid

    def load(self, recovery_id: str) -> RecoveryRecord | None:
        """Load a recovery session by ID.

        Raises RecoveryCorruptionError if the record exists but has
        corrupt message fields. Returns None if the record doesn't exist.
        """
        self.init()

        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM recovery_sessions WHERE id = ?",
                (recovery_id,),
            ).fetchone()

        if row is None:
            return None

        # Validate required fields — raises on corruption
        original_messages = _decode_message_list(
            row["original_messages"], recovery_id, "original_messages"
        )
        compressed_messages = _decode_message_list(
            row["compressed_messages"], recovery_id, "compressed_messages"
        )

        return RecoveryRecord(
            id=row["id"],
            original_messages=original_messages,
            compressed_messages=compressed_messages,
            original_tokens=row["original_tokens"],
            compressed_tokens=row["compressed_tokens"],
            compression_ratio=row["compression_ratio"],
            created_at=row["created_at"],
            session_metadata=(
                _safe_json_load(row["session_metadata"])
                if row["session_metadata"]
                else None
            ),
        )

    def delete(self, recovery_id: str) -> bool:
        """Delete a recovery session. Returns True if deleted."""
        self.init()

        with self.connect() as db:
            cur = db.execute(
                "DELETE FROM recovery_sessions WHERE id = ?",
                (recovery_id,),
            )
            if cur.rowcount > 0:
                db.execute(
                    "DELETE FROM recovery_sessions_fts WHERE id = ?",
                    (recovery_id,),
                )
            db.commit()

        return cur.rowcount > 0

    def list_recent(self, limit: int = 20) -> list[RecoveryRecord]:
        """List most recent recovery sessions.

        Skips corrupt records with a warning rather than raising.
        """
        self.init()

        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM recovery_sessions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        results: list[RecoveryRecord] = []
        for row in rows:
            rid = row["id"]
            original_messages = _validate_message_list(
                row["original_messages"], rid, "original_messages"
            )
            compressed_messages = _validate_message_list(
                row["compressed_messages"], rid, "compressed_messages"
            )
            if original_messages is None or compressed_messages is None:
                continue
            results.append(
                RecoveryRecord(
                    id=rid,
                    original_messages=original_messages,
                    compressed_messages=compressed_messages,
                    original_tokens=row["original_tokens"],
                    compressed_tokens=row["compressed_tokens"],
                    compression_ratio=row["compression_ratio"],
                    created_at=row["created_at"],
                    session_metadata=(
                        _safe_json_load(row["session_metadata"])
                        if row["session_metadata"]
                        else None
                    ),
                )
            )
        return results

    def search(self, query: str, limit: int = 10) -> list[RecoveryRecord]:
        """Search recovery sessions via FTS5 on compressed content.

        Skips corrupt records with a warning rather than raising.
        """
        self.init()

        if not self.db_path.exists():
            return []

        terms = [t for t in re.findall(r"\w+", query) if len(t) > 1]
        # Quote each term so uppercase FTS5 operators (OR, NOT, NEAR, AND)
        # are treated as literal search terms rather than FTS5 syntax
        fts_query = " OR ".join(f'"{t}"' for t in terms) if terms else '""'

        with self.connect() as db:
            rows = db.execute(
                """SELECT r.* FROM recovery_sessions r
                   JOIN recovery_sessions_fts f ON r.id = f.id
                   WHERE recovery_sessions_fts MATCH ?
                   ORDER BY f.rank LIMIT ?""",
                (fts_query, limit),
            ).fetchall()

        results: list[RecoveryRecord] = []
        for row in rows:
            rid = row["id"]
            original_messages = _validate_message_list(
                row["original_messages"], rid, "original_messages"
            )
            compressed_messages = _validate_message_list(
                row["compressed_messages"], rid, "compressed_messages"
            )
            if original_messages is None or compressed_messages is None:
                continue
            results.append(
                RecoveryRecord(
                    id=rid,
                    original_messages=original_messages,
                    compressed_messages=compressed_messages,
                    original_tokens=row["original_tokens"],
                    compressed_tokens=row["compressed_tokens"],
                    compression_ratio=row["compression_ratio"],
                    created_at=row["created_at"],
                    session_metadata=(
                        _safe_json_load(row["session_metadata"])
                        if row["session_metadata"]
                        else None
                    ),
                )
            )
        return results

    # ── Pruning ──────────────────────────────────────────────

    def _prune_impl(self, db: sqlite3.Connection) -> int:
        """Prune recovery sessions per retention policy.

        Must be called within an existing transaction (shared db connection).
        Deletes FTS rows atomically with recovery rows.

        Pruning order:
        1. Age limit (max_age_days) — oldest sessions removed first
        2. Session count limit (max_sessions) — oldest by created_at, id

        Returns the number of sessions pruned.
        """
        pruned = 0
        cutoff_id: str | None = None

        # Step 1: Age-based pruning
        if self.max_age_days is not None and self.max_age_days >= 0:
            cutoff_time = (
                datetime.now(timezone.utc) - timedelta(days=self.max_age_days)
            ).isoformat()
            old_ids = [
                row["id"]
                for row in db.execute(
                    """SELECT id FROM recovery_sessions
                       WHERE julianday(created_at) < julianday(?)
                       ORDER BY created_at ASC, id ASC""",
                    (cutoff_time,),
                ).fetchall()
            ]
            if old_ids:
                placeholders = ",".join("?" for _ in old_ids)
                db.execute(
                    f"DELETE FROM recovery_sessions_fts WHERE id IN ({placeholders})",
                    old_ids,
                )
                db.execute(
                    f"DELETE FROM recovery_sessions WHERE id IN ({placeholders})",
                    old_ids,
                )
                pruned += len(old_ids)

        # Step 2: Count-based pruning
        if self.max_sessions is not None and self.max_sessions >= 0:
            total = db.execute(
                "SELECT COUNT(*) FROM recovery_sessions"
            ).fetchone()[0]
            excess = total - self.max_sessions
            if excess > 0:
                oldest_ids = [
                    row["id"]
                    for row in db.execute(
                        """SELECT id FROM recovery_sessions
                           ORDER BY created_at ASC, id ASC
                           LIMIT ?""",
                        (excess,),
                    ).fetchall()
                ]
                if oldest_ids:
                    placeholders = ",".join("?" for _ in oldest_ids)
                    db.execute(
                        f"DELETE FROM recovery_sessions_fts WHERE id IN ({placeholders})",
                        oldest_ids,
                    )
                    db.execute(
                        f"DELETE FROM recovery_sessions WHERE id IN ({placeholders})",
                        oldest_ids,
                    )
                    pruned += len(oldest_ids)

        return pruned

    def prune(self) -> int:
        """Prune recovery sessions per retention policy.

        Public API for explicit maintenance. Applies age limit first,
        then session count limit. Returns the number of sessions pruned.
        """
        self.init()
        with self.connect() as db:
            pruned = self._prune_impl(db)
            db.commit()
        return pruned

    # ── Integrity & maintenance ──────────────────────────────

    def integrity_check(self) -> bool:
        """Run PRAGMA integrity_check."""
        if not self.db_path.exists():
            return True
        with self.connect() as db:
            result = db.execute("PRAGMA integrity_check").fetchone()
            return result[0] == "ok"

    def backup(self) -> Path:
        """Create a timestamped backup via the steward."""
        return self._steward.backup()

    def migrate(self) -> int:
        """Run pending steward migrations."""
        return self._steward.migrate()

    # ── Stats ────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return recovery store statistics."""
        self.init()

        with self.connect() as db:
            total = db.execute(
                "SELECT COUNT(*) FROM recovery_sessions"
            ).fetchone()[0]
            total_original_tokens = db.execute(
                "SELECT COALESCE(SUM(original_tokens), 0) FROM recovery_sessions"
            ).fetchone()[0]
            total_compressed_tokens = db.execute(
                "SELECT COALESCE(SUM(compressed_tokens), 0) FROM recovery_sessions"
            ).fetchone()[0]
            avg_ratio = db.execute(
                "SELECT COALESCE(AVG(compression_ratio), 0) FROM recovery_sessions"
            ).fetchone()[0]

        return {
            "total_sessions": total,
            "total_original_tokens": total_original_tokens,
            "total_compressed_tokens": total_compressed_tokens,
            "avg_compression_ratio": round(avg_ratio, 4),
            "db_version": self._steward.user_version,
        }
