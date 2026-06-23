"""Expectation Ledger v1 — behavioral expectation tracking for AI agents.

Primary API: ExpectationLedger

Tracks expectations/contracts for AI agents with trust tiers,
violation recording, and agent run tracking.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from memorant._vendor.steward import Steward
from memorant._vendor.doctor import DoctorReport, CheckResult, run_check, doctor_main
from memorant._vendor.event import AgentEvent
from memorant._vendor.flight_recorder import FlightRecorder

from .trust import TrustTier, TrustPolicy, assign_trust, redact_content
from .schema import SCHEMA_V1, MIGRATIONS


# ── Constants ──────────────────────────────────────────────────────

COMPONENT_VERSION = "1.0.0-rc.1"


# ── Helpers ────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()


def tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9_]+", text.lower()) if len(t) > 2}


def lexical_score(query: str, content: str) -> float:
    q, c = tokenize(query), tokenize(content)
    return 0.0 if not q or not c else len(q & c) / math.sqrt(len(q) * len(c))


# ── Data types ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Expectation:
    """A single behavioral expectation/contract."""

    id: str
    content: str
    source_type: str = "manual"
    source_pointer: str = ""
    trust_tier: str = "untrusted"
    status: str = "active"
    parent_contract_id: str | None = None
    metadata: dict | None = None
    score: float = 0.0  # Populated by search


@dataclass(frozen=True)
class Violation:
    """A recorded violation of an expectation."""

    id: str
    expectation_id: str
    run_id: str | None = None
    severity: str = "warning"
    evidence: str = ""
    recorded_at: str = ""


@dataclass(frozen=True)
class AgentRun:
    """An agent run tracking record."""

    id: str
    agent_id: str = ""
    session_id: str = ""
    status: str = "started"
    expectations_checked: int = 0
    violations_found: int = 0
    started_at: str = ""
    ended_at: str | None = None
    metadata: dict | None = None


@dataclass(frozen=True)
class ExpectationSearchDebug(Expectation):
    raw_rank: float = 0.0
    lexical_score_value: float = 0.0
    matched_query: str = ""


@dataclass(frozen=True)
class ExpectationEvaluation:
    expectation_id: str
    status: str
    evidence: str = ""
    run_id: str | None = None


@dataclass
class LedgerConfig:
    """ExpectationLedger configuration."""

    db_path: str | Path = "./expectations.db"
    trust_policy: TrustPolicy = field(default_factory=TrustPolicy)
    flight_recorder: FlightRecorder | None = None
    encryption_key: str | None = None


# ── ExpectationLedger (v1 primary API) ─────────────────────────────

class ExpectationLedger:
    """Primary API for Expectation Ledger v1.

    Tracks behavioral expectations for AI agents:

    - Expectation CRUD with FTS5 search
    - Contract grouping
    - Violation recording
    - Agent run tracking
    - Trust tier integration
    - Doctor contract health checks
    - SQLite steward for migrations
    - Flight recorder event logging
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        config: LedgerConfig | None = None,
    ):
        self.config = config or LedgerConfig()
        if db_path is not None:
            self.config.db_path = db_path
        self.db_path = Path(self.config.db_path)

        # Steward for migration management
        self._steward = Steward(
            self.db_path,
            encryption_key=self.config.encryption_key,
        )
        self._flight = self.config.flight_recorder

    # ── Connection management ────────────────────────────────

    def connect(self) -> sqlite3.Connection:
        """Open a database connection, with optional SQLCipher encryption."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        key = self.config.encryption_key
        if key:
            try:
                import sqlcipher3
            except ImportError:
                raise ImportError(
                    "encryption_key requires the sqlcipher3 package. "
                    "Install with: pip install memorant[encryption]"
                )
            db = sqlcipher3.connect(str(self.db_path))
            db.execute(f"PRAGMA key = '{key}'")
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

        Detects empty vs legacy by table inspection (expectations table).
        """
        target = max(MIGRATIONS) if MIGRATIONS else 0

        is_legacy = False
        if self.db_path.exists():
            try:
                with self.connect() as db:
                    row = db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='expectations'"
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

        if self._flight:
            self._flight.record(AgentEvent(
                component="expectation_ledger",
                component_version=COMPONENT_VERSION,
                event_type="store.initialized",
                severity="info",
                session_id="",
                trace_id="",
                payload={
                    "tables": list(SCHEMA_V1),
                    "version": self._steward.user_version,
                },
            ).to_dict())

        return list(SCHEMA_V1)

    # ── Expectation CRUD ─────────────────────────────────────

    def add_expectation(
        self,
        content: str,
        *,
        source_type: str = "manual",
        source_pointer: str = "",
        trust_tier: str | None = None,
        parent_contract_id: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Add an expectation with atomic deduplication.

        If an identical expectation already exists (same content_hash),
        its status is reset to 'active' if it was 'superseded'.
        Otherwise, a new expectation is created.
        """
        self.init()
        eid = str(uuid.uuid4())
        chash = content_hash(content)

        if trust_tier is None:
            trust_tier = assign_trust(
                self.config.trust_policy, source_type, source_pointer
            )

        with self.connect() as db:
            # Atomic dedup: only the primary INSERT can trigger duplicate detection
            try:
                db.execute("""
                    INSERT INTO expectations (
                        id, content, content_hash, source_type, source_pointer,
                        trust_tier, parent_contract_id, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    eid, content, chash,
                    source_type, source_pointer, trust_tier,
                    parent_contract_id,
                    json.dumps(metadata or {}),
                ))
            except sqlite3.IntegrityError:
                # Duplicate content_hash — reactivate if superseded
                existing = db.execute(
                    "SELECT id, status FROM expectations WHERE content_hash = ?",
                    (chash,),
                ).fetchone()
                if existing:
                    eid = existing["id"]
                    if existing["status"] == "superseded":
                        db.execute(
                            "UPDATE expectations SET status = 'active', "
                            "updated_at = datetime('now') WHERE id = ?",
                            (eid,),
                        )
                else:
                    raise
            else:
                # Only insert into FTS if primary insert succeeded
                db.execute(
                    "INSERT INTO expectations_fts (id, content) VALUES (?, ?)",
                    (eid, content),
                )

            db.commit()

        if self._flight:
            self._flight.record(AgentEvent(
                component="expectation_ledger",
                component_version=COMPONENT_VERSION,
                event_type="expectation.added",
                severity="info",
                session_id="",
                trace_id="",
                payload={"expectation_id": eid, "trust_tier": trust_tier},
            ).to_dict())

        return eid

    def get_expectation(self, expectation_id: str) -> Expectation | None:
        """Retrieve a single expectation by ID."""
        self.init()
        with self.connect() as db:
            row = db.execute(
                "SELECT id, content, source_type, source_pointer, trust_tier, "
                "status, parent_contract_id, metadata "
                "FROM expectations WHERE id = ?",
                (expectation_id,),
            ).fetchone()

        if row is None:
            return None

        meta = row["metadata"]
        try:
            meta = json.loads(meta) if meta else None
        except (json.JSONDecodeError, TypeError):
            meta = None

        return Expectation(
            id=row["id"],
            content=row["content"],
            source_type=row["source_type"],
            source_pointer=row["source_pointer"],
            trust_tier=row["trust_tier"],
            status=row["status"],
            parent_contract_id=row["parent_contract_id"],
            metadata=meta,
        )

    def update_expectation(
        self,
        expectation_id: str,
        *,
        content: str | None = None,
        status: str | None = None,
        trust_tier: str | None = None,
        source_pointer: str | None = None,
        metadata: dict | None = None,
    ) -> bool:
        """Update fields on an existing expectation. Returns True if updated."""
        self.init()

        fields = []
        params: list = []

        if content is not None:
            fields.append("content = ?")
            fields.append("content_hash = ?")
            params.append(content)
            params.append(content_hash(content))
        if status is not None:
            fields.append("status = ?")
            params.append(status)
        if trust_tier is not None:
            fields.append("trust_tier = ?")
            params.append(trust_tier)
        if source_pointer is not None:
            fields.append("source_pointer = ?")
            params.append(source_pointer)
        if metadata is not None:
            fields.append("metadata = ?")
            params.append(json.dumps(metadata))

        if not fields:
            return False

        fields.append("updated_at = datetime('now')")
        params.append(expectation_id)

        with self.connect() as db:
            cur = db.execute(
                f"UPDATE expectations SET {', '.join(fields)} WHERE id = ?",
                params,
            )
            if cur.rowcount > 0 and content is not None:
                # Update FTS index
                db.execute(
                    "UPDATE expectations_fts SET content = ? WHERE id = ?",
                    (content, expectation_id),
                )
            db.commit()

        return cur.rowcount > 0

    def delete_expectation(self, expectation_id: str) -> bool:
        """Delete an expectation entirely. Returns True if deleted.

        Fails with False if the expectation has recorded violations
        (foreign key constraint). Use status='superseded' to retire
        an expectation with history instead.
        """
        self.init()
        with self.connect() as db:
            try:
                cur = db.execute(
                    "DELETE FROM expectations WHERE id = ?",
                    (expectation_id,),
                )
                if cur.rowcount > 0:
                    db.execute(
                        "DELETE FROM expectations_fts WHERE id = ?",
                        (expectation_id,),
                    )
                db.commit()
            except sqlite3.IntegrityError:
                return False

        return cur.rowcount > 0

    # ── Search ───────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        min_trust: str | None = None,
        status: str | None = "active",
    ) -> list[Expectation]:
        """Search expectations using FTS5 with lexical scoring."""
        return [
            Expectation(
                id=r.id,
                content=r.content,
                source_type=r.source_type,
                source_pointer=r.source_pointer,
                trust_tier=r.trust_tier,
                status=r.status,
                parent_contract_id=r.parent_contract_id,
                metadata=r.metadata,
                score=r.score,
            )
            for r in self.search_debug(
                query,
                limit=limit,
                min_trust=min_trust,
                status=status,
            )
        ]

    def search_debug(
        self,
        query: str,
        *,
        limit: int = 10,
        min_trust: str | None = None,
        status: str | None = "active",
    ) -> list[ExpectationSearchDebug]:
        """Search expectations and include FTS rank diagnostics."""
        self.init()

        if not self.db_path.exists():
            return []

        terms = [t for t in re.findall(r'\w+', query) if len(t) > 1]
        # Quote each term so uppercase FTS5 operators (OR, NOT, NEAR, AND)
        # are treated as literal search terms rather than FTS5 syntax
        fts_query = " OR ".join(f'"{t}"' for t in terms) if terms else '""'

        trust_ranks = {"operator": 0, "verified": 1, "derived": 2, "untrusted": 3}

        with self.connect() as db:
            sql = """
                SELECT
                    e.id,
                    e.content,
                    e.source_type,
                    e.source_pointer,
                    e.trust_tier,
                    e.status,
                    e.parent_contract_id,
                    e.metadata,
                    f.rank AS raw_rank
                FROM expectations_fts f
                JOIN expectations e ON f.id = e.id
                WHERE expectations_fts MATCH ?
            """
            params: list = [fts_query]

            if status:
                sql += " AND e.status = ?"
                params.append(status)

            if min_trust:
                allowed = [
                    t for t in trust_ranks
                    if trust_ranks[t] <= trust_ranks.get(min_trust, 3)
                ]
                sql += " AND e.trust_tier IN ("
                sql += ",".join("?" for _ in allowed) + ")"
                params.extend(allowed)

            sql += " ORDER BY f.rank LIMIT ?"
            params.append(limit * 3)  # Fetch more for rescoring

            rows = db.execute(sql, params).fetchall()

        if not rows:
            return []

        # Compute lexical scores and sort
        results: list[ExpectationSearchDebug] = []
        for r in rows:
            score = lexical_score(query, r["content"])
            meta = r["metadata"]
            try:
                meta = json.loads(meta) if meta else None
            except (json.JSONDecodeError, TypeError):
                meta = None

            results.append(ExpectationSearchDebug(
                id=r["id"],
                content=r["content"],
                source_type=r["source_type"],
                source_pointer=r["source_pointer"],
                trust_tier=r["trust_tier"],
                status=r["status"],
                parent_contract_id=r["parent_contract_id"],
                metadata=meta,
                score=score,
                raw_rank=r["raw_rank"],
                lexical_score_value=score,
                matched_query=fts_query,
            ))

        results.sort(key=lambda x: -x.score)
        return results[:limit]

    def evaluate_expectation(
        self,
        expectation_id: str,
        *,
        passed: bool | None,
        evidence: str = "",
        run_id: str | None = None,
        violation_severity: str = "warning",
    ) -> ExpectationEvaluation:
        """Record a pass/fail/unknown evaluation with optional evidence."""
        self.init()
        exp = self.get_expectation(expectation_id)
        if exp is None:
            raise ValueError(f"Expectation not found: {expectation_id}")

        if passed is False:
            vid = str(uuid.uuid4())
            with self.connect() as db:
                if run_id:
                    db.execute("""
                        INSERT OR REPLACE INTO run_expectations
                            (run_id, expectation_id, passed, checked_at)
                        VALUES (?, ?, 0, datetime('now'))
                    """, (run_id, expectation_id))
                    db.execute(
                        "UPDATE runs SET expectations_checked = ("
                        "  SELECT COUNT(*) FROM run_expectations "
                        "  WHERE run_id = ? AND passed = 1"
                        ") WHERE id = ?",
                        (run_id, run_id),
                    )
                db.execute(
                    "UPDATE expectations SET status = 'violated', "
                    "updated_at = datetime('now') WHERE id = ?",
                    (expectation_id,),
                )
                db.execute("""
                    INSERT INTO violations (id, expectation_id, run_id, severity, evidence)
                    VALUES (?, ?, ?, ?, ?)
                """, (vid, expectation_id, run_id, violation_severity, evidence))
                if run_id:
                    db.execute(
                        "UPDATE runs SET violations_found = violations_found + 1 "
                        "WHERE id = ?",
                        (run_id,),
                    )
                db.commit()

            if self._flight:
                self._flight.record(AgentEvent(
                    component="expectation_ledger",
                    component_version=COMPONENT_VERSION,
                    event_type="violation.recorded",
                    severity=violation_severity,
                    session_id="",
                    trace_id="",
                    payload={
                        "violation_id": vid,
                        "expectation_id": expectation_id,
                        "run_id": run_id,
                    },
                ).to_dict())

            return ExpectationEvaluation(expectation_id, "fail", evidence, run_id)

        if run_id and passed is not None:
            self.check_expectation(run_id, expectation_id, passed=bool(passed))

        if passed is True:
            return ExpectationEvaluation(expectation_id, "pass", evidence, run_id)
        return ExpectationEvaluation(expectation_id, "unknown", evidence, run_id)

    # ── Violation recording ──────────────────────────────────

    def record_violation(
        self,
        expectation_id: str,
        *,
        severity: str = "warning",
        evidence: str = "",
        run_id: str | None = None,
    ) -> str:
        """Record a violation of an expectation. Returns violation ID."""
        self.init()
        vid = str(uuid.uuid4())

        with self.connect() as db:
            # Mark expectation as violated
            db.execute(
                "UPDATE expectations SET status = 'violated', "
                "updated_at = datetime('now') WHERE id = ?",
                (expectation_id,),
            )
            db.execute("""
                INSERT INTO violations (id, expectation_id, run_id, severity, evidence)
                VALUES (?, ?, ?, ?, ?)
            """, (vid, expectation_id, run_id, severity, evidence))

            # Update run violation count if run_id provided
            if run_id:
                db.execute(
                    "UPDATE runs SET violations_found = violations_found + 1 "
                    "WHERE id = ?",
                    (run_id,),
                )

            db.commit()

        if self._flight:
            self._flight.record(AgentEvent(
                component="expectation_ledger",
                component_version=COMPONENT_VERSION,
                event_type="violation.recorded",
                severity=severity,
                session_id="",
                trace_id="",
                payload={
                    "violation_id": vid,
                    "expectation_id": expectation_id,
                    "run_id": run_id,
                },
            ).to_dict())

        return vid

    def get_violations(
        self,
        expectation_id: str | None = None,
        run_id: str | None = None,
        *,
        limit: int = 50,
    ) -> list[Violation]:
        """Retrieve violations, optionally filtered."""
        self.init()
        with self.connect() as db:
            sql = "SELECT id, expectation_id, run_id, severity, evidence, recorded_at FROM violations WHERE 1=1"
            params: list = []

            if expectation_id:
                sql += " AND expectation_id = ?"
                params.append(expectation_id)
            if run_id:
                sql += " AND run_id = ?"
                params.append(run_id)

            sql += " ORDER BY recorded_at DESC LIMIT ?"
            params.append(limit)

            rows = db.execute(sql, params).fetchall()

        return [
            Violation(
                id=r["id"],
                expectation_id=r["expectation_id"],
                run_id=r["run_id"],
                severity=r["severity"],
                evidence=r["evidence"],
                recorded_at=r["recorded_at"],
            )
            for r in rows
        ]

    # ── Agent runs ───────────────────────────────────────────

    def start_run(
        self,
        *,
        agent_id: str = "",
        session_id: str = "",
        metadata: dict | None = None,
    ) -> str:
        """Start a new agent run. Returns run ID."""
        self.init()
        rid = str(uuid.uuid4())

        with self.connect() as db:
            db.execute("""
                INSERT INTO runs (id, agent_id, session_id, metadata)
                VALUES (?, ?, ?, ?)
            """, (rid, agent_id, session_id, json.dumps(metadata or {})))
            db.commit()

        return rid

    def end_run(
        self,
        run_id: str,
        *,
        status: str = "completed",
        metadata: dict | None = None,
    ) -> bool:
        """End an agent run. Returns True if updated."""
        self.init()

        updates = ["status = ?", "ended_at = datetime('now')"]
        params: list = [status]

        if metadata is not None:
            updates.append("metadata = ?")
            params.append(json.dumps(metadata))

        params.append(run_id)

        with self.connect() as db:
            cur = db.execute(
                f"UPDATE runs SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            db.commit()

        return cur.rowcount > 0

    def check_expectation(
        self,
        run_id: str,
        expectation_id: str,
        *,
        passed: bool = True,
    ) -> None:
        """Record that an expectation was checked during a run.

        Uses INSERT OR REPLACE for the join row. The run's
        expectations_checked counter is only incremented on first
        pass — repeat checks of the same expectation don't double-count.
        """
        self.init()
        with self.connect() as db:
            db.execute("""
                INSERT OR REPLACE INTO run_expectations
                    (run_id, expectation_id, passed, checked_at)
                VALUES (?, ?, ?, datetime('now'))
            """, (run_id, expectation_id, 1 if passed else 0))

            # Derive expectations_checked from the join table — always correct
            db.execute(
                "UPDATE runs SET expectations_checked = ("
                "  SELECT COUNT(*) FROM run_expectations "
                "  WHERE run_id = ? AND passed = 1"
                ") WHERE id = ?",
                (run_id, run_id),
            )

            db.commit()

    def get_run(self, run_id: str) -> AgentRun | None:
        """Retrieve a single agent run by ID."""
        self.init()
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone()

        if row is None:
            return None

        meta = row["metadata"]
        try:
            meta = json.loads(meta) if meta else None
        except (json.JSONDecodeError, TypeError):
            meta = None

        return AgentRun(
            id=row["id"],
            agent_id=row["agent_id"] or "",
            session_id=row["session_id"] or "",
            status=row["status"],
            expectations_checked=row["expectations_checked"] or 0,
            violations_found=row["violations_found"] or 0,
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            metadata=meta,
        )

    # ── Contracts ────────────────────────────────────────────

    def add_contract(
        self,
        name: str,
        *,
        description: str = "",
        version: str = "1.0",
        source_pointer: str = "",
        trust_tier: str | None = None,
    ) -> str:
        """Add a contract grouping. Returns contract ID."""
        self.init()
        cid = str(uuid.uuid4())

        if trust_tier is None:
            trust_tier = assign_trust(
                self.config.trust_policy, "contract", source_pointer
            )

        with self.connect() as db:
            db.execute("""
                INSERT INTO contracts (id, name, description, version, source_pointer, trust_tier)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (cid, name, description, version, source_pointer, trust_tier))
            db.commit()

        return cid

    def get_contract(self, contract_id: str) -> dict | None:
        """Retrieve a contract by ID."""
        self.init()
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM contracts WHERE id = ?", (contract_id,)
            ).fetchone()
        return dict(row) if row else None

    # ── Integrity & backups ──────────────────────────────────

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

    # ── Doctor ────────────────────────────────────────────────

    def doctor(self, json_output: bool = False) -> int:
        """Run health checks per the Agent Integrity doctor contract."""
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
        return doctor_main(
            "expectation_ledger",
            COMPONENT_VERSION,
            checks,
            json_output=json_output,
        )

    # ── Stats ─────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return ledger statistics."""
        self.init()
        with self.connect() as db:
            total_exp = db.execute(
                "SELECT COUNT(*) FROM expectations"
            ).fetchone()[0]
            active_exp = db.execute(
                "SELECT COUNT(*) FROM expectations WHERE status = 'active'"
            ).fetchone()[0]
            violated_exp = db.execute(
                "SELECT COUNT(*) FROM expectations WHERE status = 'violated'"
            ).fetchone()[0]
            by_trust = {
                row["trust_tier"]: row["cnt"]
                for row in db.execute(
                    "SELECT trust_tier, COUNT(*) as cnt FROM expectations "
                    "GROUP BY trust_tier"
                ).fetchall()
            }
            by_status = {
                row["status"]: row["cnt"]
                for row in db.execute(
                    "SELECT status, COUNT(*) as cnt FROM expectations "
                    "GROUP BY status"
                ).fetchall()
            }
            total_runs = db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            total_violations = db.execute(
                "SELECT COUNT(*) FROM violations"
            ).fetchone()[0]
            total_contracts = db.execute(
                "SELECT COUNT(*) FROM contracts"
            ).fetchone()[0]

        return {
            "total_expectations": total_exp,
            "active_expectations": active_exp,
            "violated_expectations": violated_exp,
            "by_trust": by_trust,
            "by_status": by_status,
            "total_runs": total_runs,
            "total_violations": total_violations,
            "total_contracts": total_contracts,
            "db_version": self._steward.user_version,
        }
