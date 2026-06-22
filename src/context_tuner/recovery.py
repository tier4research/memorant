"""Recovery store for Context Tuner — SQLite + FTS5 backed rollback.

Stores original messages for each compression session so they can be
recovered (decompressed) later. Uses FTS5 for searching compressed content.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memorant._vendor.steward import Steward

from .schema import SCHEMA_V1, MIGRATIONS


# ── Helpers ──────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        busy_timeout_ms: int = 5000,
        encryption_key: str | None = None,
    ):
        self.db_path = Path(db_path)
        self._steward = Steward(
            self.db_path,
            busy_timeout_ms=busy_timeout_ms,
            encryption_key=encryption_key,
        )
        self._encryption_key = encryption_key

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
        """Initialize database schema and run migrations."""
        existed = self.db_path.exists()
        existing_version = self._steward.user_version if existed else 0

        with self.connect() as db:
            for sql in SCHEMA_V1.values():
                db.execute(sql)
            db.commit()

        target = max(MIGRATIONS) if MIGRATIONS else 0
        self._steward.initialize(target)

        for version, sql in sorted(MIGRATIONS.items()):
            self._steward.add_migration(version, sql)

        if existed and existing_version < target:
            self._steward.migrate()
        elif not existed:
            with self.connect() as db:
                db.execute(f"PRAGMA user_version = {target}")
                db.commit()

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
        """
        self.init()
        rid = recovery_id or str(uuid.uuid4())

        original_json = json.dumps(original_messages, ensure_ascii=False)
        compressed_json = json.dumps(compressed_messages, ensure_ascii=False)

        # Build searchable content from compressed messages
        searchable = " ".join(
            m.get("content", "") for m in compressed_messages
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
            db.commit()

        return rid

    def load(self, recovery_id: str) -> RecoveryRecord | None:
        """Load a recovery session by ID."""
        self.init()

        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM recovery_sessions WHERE id = ?",
                (recovery_id,),
            ).fetchone()

        if row is None:
            return None

        return RecoveryRecord(
            id=row["id"],
            original_messages=json.loads(row["original_messages"]),
            compressed_messages=json.loads(row["compressed_messages"]),
            original_tokens=row["original_tokens"],
            compressed_tokens=row["compressed_tokens"],
            compression_ratio=row["compression_ratio"],
            created_at=row["created_at"],
            session_metadata=(
                json.loads(row["session_metadata"])
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
        """List most recent recovery sessions."""
        self.init()

        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM recovery_sessions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        return [
            RecoveryRecord(
                id=row["id"],
                original_messages=json.loads(row["original_messages"]),
                compressed_messages=json.loads(row["compressed_messages"]),
                original_tokens=row["original_tokens"],
                compressed_tokens=row["compressed_tokens"],
                compression_ratio=row["compression_ratio"],
                created_at=row["created_at"],
                session_metadata=(
                    json.loads(row["session_metadata"])
                    if row["session_metadata"]
                    else None
                ),
            )
            for row in rows
        ]

    def search(self, query: str, limit: int = 10) -> list[RecoveryRecord]:
        """Search recovery sessions via FTS5 on compressed content."""
        self.init()

        if not self.db_path.exists():
            return []

        terms = [t for t in re.findall(r"\w+", query) if len(t) > 1]
        fts_query = " OR ".join(terms) if terms else '""'

        with self.connect() as db:
            rows = db.execute(
                """SELECT r.* FROM recovery_sessions r
                   JOIN recovery_sessions_fts f ON r.id = f.id
                   WHERE recovery_sessions_fts MATCH ?
                   ORDER BY f.rank LIMIT ?""",
                (fts_query, limit),
            ).fetchall()

        return [
            RecoveryRecord(
                id=row["id"],
                original_messages=json.loads(row["original_messages"]),
                compressed_messages=json.loads(row["compressed_messages"]),
                original_tokens=row["original_tokens"],
                compressed_tokens=row["compressed_tokens"],
                compression_ratio=row["compression_ratio"],
                created_at=row["created_at"],
                session_metadata=(
                    json.loads(row["session_metadata"])
                    if row["session_metadata"]
                    else None
                ),
            )
            for row in rows
        ]

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
