"""Dependency-free SQLite steward for schema migration, integrity checks, and backups.

Vendored under _vendor namespace by Tier 4 packages. No dependencies outside stdlib.

Features:
- PRAGMA user_version tracking
- WAL + bounded busy timeout
- Pre-migration integrity check + timestamped backup
- Ordered transactional migrations
- Interrupted-migration recovery
- Post-migration integrity verification
- Canary-based migration validation
"""

from __future__ import annotations

import os
import shutil
import sqlite3

from .sqlcipher import apply_key
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


class StewardError(Exception):
    """Base exception for steward operations."""


class IntegrityCheckFailed(StewardError):
    """Pre- or post-migration integrity check failed."""


class MigrationFailed(StewardError):
    """A migration step failed and was rolled back."""


class VersionMismatch(StewardError):
    """Database user_version does not match expected version."""


class Steward:
    """Manages SQLite database lifecycle: migrations, integrity, backups.

    Usage:
        steward = Steward("path/to/db.sqlite")
        steward.initialize(target_version=2)
        steward.add_migration(1, "CREATE TABLE IF NOT EXISTS events (...)")
        steward.add_migration(2, "ALTER TABLE events ADD COLUMN tags TEXT")
        steward.migrate()
    """

    def __init__(self, db_path: str | Path, busy_timeout_ms: int = 5000, encryption_key: str | None = None):
        self.db_path = Path(db_path)
        self.busy_timeout_ms = busy_timeout_ms
        self._migrations: dict[int, str] = {}
        self._target_version: int = 0
        self._encryption_key = encryption_key

    # ── Connection management ──────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        """Open a connection with WAL, foreign keys, and busy timeout.

        If encryption_key was provided, uses sqlcipher3 for encrypted storage.
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        if self._encryption_key:
            import sqlcipher3
            db = sqlcipher3.connect(str(self.db_path))
            apply_key(db, self._encryption_key)
        else:
            db = sqlite3.connect(str(self.db_path))

        db.execute(f"PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        return db

    @property
    def user_version(self) -> int:
        """Read the current database user_version."""
        if not self.db_path.exists():
            return 0
        with self._connect() as db:
            return db.execute("PRAGMA user_version").fetchone()[0]

    # ── Initialization ──────────────────────────────────────────────

    def initialize(self, target_version: int) -> None:
        """Set the target schema version.

        The database is created on first use, not here. This just records intent.
        """
        self._target_version = target_version

    def add_migration(self, version: int, sql: str) -> None:
        """Register a migration SQL for the given version number.

        Migrations are applied in version order. Each must be idempotent
        (use IF NOT EXISTS / IF EXISTS patterns).
        """
        self._migrations[version] = sql.strip()

    # ── Backup ──────────────────────────────────────────────────────

    def _backup_path(self) -> Path:
        """Generate a timestamped backup path."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return self.db_path.with_suffix(f".backup-{ts}.db")

    def backup(self) -> Path:
        """Create a file-copy backup of the database.

        Returns the backup path. Checkpoints WAL first so backup is complete.
        """
        if not self.db_path.exists():
            raise StewardError(f"Database does not exist: {self.db_path}")

        # Checkpoint WAL so the main file is current
        with self._connect() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        dest = self._backup_path()
        shutil.copy2(str(self.db_path), str(dest))
        return dest

    # ── Integrity ───────────────────────────────────────────────────

    def integrity_check(self) -> bool:
        """Run PRAGMA integrity_check. Returns True if database is healthy."""
        if not self.db_path.exists():
            return True
        with self._connect() as db:
            result = db.execute("PRAGMA integrity_check").fetchone()
            return result[0] == "ok"

    def _verify_integrity(self) -> None:
        """Run integrity check and raise if it fails."""
        if not self.integrity_check():
            raise IntegrityCheckFailed(
                f"Database integrity check failed for {self.db_path}"
            )

    # ── Migration ────────────────────────────────────────────────────

    def migrate(self) -> int:
        """Run pending migrations in version order.

        Returns the new user_version. Raises MigrationFailed on error.

        Migration process:
        1. Verify pre-migration integrity
        2. Create timestamped backup
        3. For each pending migration, from current version to target:
           a. Record canary (migration_v<next>_started)
           b. Execute migration SQL in a transaction
           c. Update user_version
           d. Clear canary
        4. Verify post-migration integrity
        5. Return new version

        Interrupted migrations are detected on next run via canary table
        and rolled back to the last known-good version.
        """
        current = self.user_version
        target = self._target_version

        if current == target:
            return current

        if current > target:
            raise VersionMismatch(
                f"Database version {current} exceeds target {target}. "
                f"Downgrades are not supported."
            )

        # Ensure the canary table exists
        self._ensure_canary_table()

        # Pre-migration integrity check
        if self.db_path.exists():
            self._verify_integrity()

        # Backup
        backup_path = self.backup() if self.db_path.exists() else None

        # Apply migrations in order
        first_migration = True
        try:
            for version in sorted(self._migrations):
                if version <= current:
                    continue

                sql = self._migrations[version]
                self._apply_single_migration(version, sql)
                current = version
                first_migration = False

        except Exception as e:
            # Only restore from backup if the very first migration failed.
            # For later migrations, the per-migration transaction rollback
            # (via the context manager) is sufficient.
            if first_migration and backup_path and backup_path.exists():
                shutil.copy2(str(backup_path), str(self.db_path))
                # Remove WAL/SHM side-files
                for suffix in (".db-wal", ".db-shm"):
                    side = self.db_path.with_name(self.db_path.name + suffix)
                    if side.exists():
                        side.unlink()
            raise MigrationFailed(
                f"Migration to version {version} failed: {e}"
            ) from e

        # Post-migration integrity check
        self._verify_integrity()

        return self.user_version

    def _ensure_canary_table(self) -> None:
        """Create the migration canary table if it doesn't exist."""
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS _steward_canary (
                    version INTEGER PRIMARY KEY,
                    started_at TEXT NOT NULL DEFAULT (datetime('now')),
                    completed INTEGER DEFAULT 0
                )"""
            )
            db.commit()

            # Check for interrupted migrations
            interrupted = db.execute(
                "SELECT version FROM _steward_canary WHERE completed = 0 ORDER BY version DESC"
            ).fetchone()

            if interrupted:
                interrupted_version = interrupted[0]
                # Rollback: set user_version to before the interrupted migration
                rollback_to = interrupted_version - 1
                db.execute(f"PRAGMA user_version = {rollback_to}")
                db.execute(
                    "UPDATE _steward_canary SET completed = -1, "
                    "started_at = started_at || ' (rolled back)' WHERE version = ?",
                    (interrupted_version,),
                )
                db.commit()

    def _apply_single_migration(self, version: int, sql: str) -> None:
        """Apply one migration inside a transaction with canary tracking.

        The connection context manager provides atomicity: commit on success,
        rollback on exception. No explicit BEGIN/COMMIT/ROLLBACK needed.
        """
        with self._connect() as db:
            # Set canary
            db.execute(
                "INSERT OR REPLACE INTO _steward_canary (version, started_at, completed) "
                "VALUES (?, datetime('now'), 0)",
                (version,),
            )

            # Execute migration statements
            for statement in sql.split(";"):
                statement = statement.strip()
                if statement:
                    db.execute(statement)

            # Update version
            db.execute(f"PRAGMA user_version = {version}")

            # Clear canary
            db.execute(
                "UPDATE _steward_canary SET completed = 1 WHERE version = ?",
                (version,),
            )

    # ── Verification ─────────────────────────────────────────────────

    def verify_version(self, expected: int) -> bool:
        """Check that the database is at the expected version."""
        return self.user_version == expected

    def check_migration_complete(self) -> bool:
        """Verify all registered migrations completed successfully."""
        return self.user_version == self._target_version

    def migration_status(self) -> dict[str, int]:
        """Return {current_version: int, target_version: int, pending: int}."""
        current = self.user_version
        return {
            "current_version": current,
            "target_version": self._target_version,
            "pending": max(0, self._target_version - current),
        }
