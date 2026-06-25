"""Memorant v1 — trustworthy memory substrate for AI agents.

Primary API: MemorantStore
Deprecated alias: MemoryPalace (for backward compat)
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import time
import uuid
from collections.abc import Iterable as IterableABC
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Protocol

from ._vendor.steward import Steward
from ._vendor.sqlcipher import apply_key
from ._vendor.doctor import DoctorReport, CheckResult, run_check, doctor_main
from ._vendor.event import AgentEvent
from ._vendor.flight_recorder import FlightRecorder
from .retriever import Retriever, FTSRetriever, SearchResult, SearchDebugResult

from .trust import (
    TrustTier,
    TrustPolicy,
    assign_trust,
    redact_content,
    REDACT_PATTERNS,
)
from .schema import SCHEMA_V1, MIGRATIONS

# ── Constants ──────────────────────────────────────────────────────

RESONANCE_DEADLINE_MS = 100
RESONANCE_COOLDOWN_MS = 5000
MAX_RESONANCE_LINES = 3
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


def _coerce_string_list(value: Iterable[str] | str | None) -> list[str]:
    """Normalize optional string iterables into a JSON-storable list."""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        if isinstance(decoded, list):
            return [str(item) for item in decoded]
        return [str(decoded)]
    if isinstance(value, IterableABC):
        return [str(item) for item in value]
    return [str(value)]


# ── Data types ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Claim:
    id: str
    content: str
    score: float = 0.0
    source_pointer: str = ""
    reinforcement_count: int = 0
    trust_tier: str = "untrusted"


@dataclass(frozen=True)
class ClaimSearchDebug:
    id: str
    content: str
    score: float
    source_pointer: str
    reinforcement_count: int
    trust_tier: str
    rank: float
    relevance: float
    reinforcement_bonus: float
    recency_bonus: float
    matched_query: str


@dataclass(frozen=True)
class HygieneReport:
    stale_claims: list[str]
    duplicate_groups: list[list[str]]
    contradiction_pairs: list[tuple[str, str]]
    broken_derived_claims: list[str]
    frequently_retrieved_untrusted: list[str]

    def to_dict(self) -> dict:
        return {
            "stale_claims": self.stale_claims,
            "duplicate_groups": self.duplicate_groups,
            "contradiction_pairs": self.contradiction_pairs,
            "broken_derived_claims": self.broken_derived_claims,
            "frequently_retrieved_untrusted": self.frequently_retrieved_untrusted,
        }


@dataclass
class StoreConfig:
    """MemorantStore configuration."""
    db_path: str | Path = "./memorant.db"
    trust_policy: TrustPolicy = field(default_factory=TrustPolicy)
    resonance_deadline_ms: int = RESONANCE_DEADLINE_MS
    resonance_floor: float = 0.12
    flight_recorder: FlightRecorder | None = None
    retention_mode: str = "full"  # full | minimal | none
    encryption_key: str | None = None  # SQLCipher encryption key (requires memorant[encryption])


# ── MemorantStore (v1 primary API) ─────────────────────────────────

class MemorantStore:
    """Primary API for Memorant v1.

    Replaces MemoryPalace with stronger guarantees:
    - Atomic INSERT...ON CONFLICT deduplication
    - Trust tiers with policy-based assignment
    - Field-aware secret redaction
    - Transactional invalidation with relation tracking
    - FTS5 retrieval with scoring (rank + reinforcement + recency)
    - 100ms resonance deadline with degraded fallback
    - Atomic digest promotion
    - SQLite steward for migrations

    The old MemoryPalace class is retained as a deprecated alias.
    """

    def __init__(self, db_path: str | Path | None = None, config: StoreConfig | None = None):
        self.config = config or StoreConfig()
        if db_path is not None:
            self.config.db_path = db_path
        self.db_path = Path(self.config.db_path)

        # Steward for migration management
        self._steward = Steward(self.db_path, encryption_key=self.config.encryption_key)
        self._flight = self.config.flight_recorder

    # ── Connection management ────────────────────────────────

    def connect(self) -> sqlite3.Connection:
        """Open a database connection, with optional SQLCipher encryption.

        If encryption_key is set in config, uses sqlcipher3 for encrypted
        storage. Without it, uses standard Python sqlite3.

        Raises ImportError if encryption_key is set but sqlcipher3 is not
        installed. Install with: pip install memorant[encryption]
        """
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
            apply_key(db, key)
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

        Detects empty vs legacy databases by table inspection:
        - No file → create schema fresh, set target version
        - File exists, no primary table → pre-created empty → create schema fresh
        - File exists, primary table present → legacy DB → skip schema, run migrations
        - Already at target version → no-op
        - Newer than target → error (no downgrades)
        """
        target = max(MIGRATIONS) if MIGRATIONS else 0

        # Determine if this is a legacy database by checking for the primary table
        is_legacy = False
        if self.db_path.exists():
            try:
                with self.connect() as db:
                    row = db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='claim_units'"
                    ).fetchone()
                    is_legacy = row is not None
            except Exception:
                # Can't open DB (encrypted, corrupt) — treat as fresh
                is_legacy = False

        if is_legacy:
            # Legacy DB: don't touch schema, run migrations through steward
            self._steward.initialize(target)
            for version, sql in sorted(MIGRATIONS.items()):
                self._steward.add_migration(version, sql)
            self._steward.migrate()
        else:
            # Fresh DB (or pre-created empty file): create schema, set version
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
                component="memorant",
                component_version=COMPONENT_VERSION,
                event_type="store.initialized",
                severity="info",
                session_id="",
                trace_id="",
                payload={"tables": list(SCHEMA_V1), "version": self._steward.user_version},
            ).to_dict())

        return list(SCHEMA_V1)

    # ── Claim CRUD ───────────────────────────────────────────

    def add_claim(
        self,
        content: str,
        *,
        source_pointer: str,
        source_type: str = "manual",
        trust_tier: str | None = None,
        fact_refs: Iterable[str] | None = None,
        valid_from: str | None = None,
        emotional_markers: Iterable[str] | None = None,
        derived_from_ids: Iterable[str] | None = None,
    ) -> str:
        """Add a claim with atomic deduplication.

        If an identical claim already exists (same content_hash), its
        reinforcement_count is incremented. Otherwise, a new claim is created.

        Trust tier is assigned via policy if not explicitly provided.
        """
        self.init()
        cid = str(uuid.uuid4())
        chash = content_hash(content)

        explicit_trust_tier = trust_tier is not None
        if trust_tier is None:
            trust_tier = assign_trust(self.config.trust_policy, source_type, source_pointer)

        fact_refs = _coerce_string_list(fact_refs)
        emotional_markers = _coerce_string_list(emotional_markers)
        derived_from_ids = _coerce_string_list(derived_from_ids)

        with self.connect() as db:
            # Atomic dedup: only the primary INSERT can trigger duplicate detection
            try:
                db.execute("""
                    INSERT INTO claim_units (id, content, content_hash, fact_refs,
                        source_type, source_pointer, trust_tier, valid_from,
                        emotional_markers)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    cid, content, chash,
                    json.dumps(fact_refs),
                    source_type, source_pointer, trust_tier,
                    valid_from,
                    json.dumps(emotional_markers),
                ))
            except sqlite3.IntegrityError:
                # Duplicate content_hash — increment existing
                existing = db.execute(
                    "SELECT id FROM claim_units WHERE content_hash = ?",
                    (chash,),
                ).fetchone()
                if existing:
                    db.execute(
                        "UPDATE claim_units SET reinforcement_count = reinforcement_count + 1, "
                        "last_touched = datetime('now') WHERE id = ?",
                        (existing["id"],),
                    )
                    cid = existing["id"]
                else:
                    raise  # Not a duplicate — re-raise the original IntegrityError
            else:
                # Only insert into FTS if primary insert succeeded
                db.execute(
                    "INSERT INTO claim_fts (id, content) VALUES (?, ?)",
                    (cid, content),
                )

            # Record derived_from relations
            if derived_from_ids:
                for src_id in derived_from_ids:
                    db.execute(
                        "INSERT OR IGNORE INTO derived_from (derived_id, source_id) VALUES (?, ?)",
                        (cid, src_id),
                    )

                # Inherit minimum trust from sources
                if not explicit_trust_tier or trust_tier == "derived":
                    sources = db.execute(
                        "SELECT trust_tier FROM claim_units WHERE id IN ({})".format(
                            ",".join("?" for _ in derived_from_ids)
                        ),
                        derived_from_ids,
                    ).fetchall()
                    if sources:
                        min_trust = max(
                            (s["trust_tier"] for s in sources),
                            key=lambda t: TrustTier.rank(t),
                        )
                        db.execute(
                            "UPDATE claim_units SET trust_tier = ? WHERE id = ?",
                            (min_trust, cid),
                        )

            db.commit()

        if self._flight:
            self._flight.record(AgentEvent(
                component="memorant",
                component_version=COMPONENT_VERSION,
                event_type="claim.added",
                severity="info",
                session_id="",
                trace_id="",
                payload={"claim_id": cid, "trust_tier": trust_tier},
            ).to_dict())

        return cid

    def get_claim(self, claim_id: str) -> Claim | None:
        """Retrieve a single claim by ID."""
        self.init()
        with self.connect() as db:
            row = db.execute(
                "SELECT id, content, source_pointer, reinforcement_count, trust_tier "
                "FROM claim_units WHERE id = ? AND is_valid = 1",
                (claim_id,),
            ).fetchone()
        if row is None:
            return None
        return Claim(
            id=row["id"],
            content=row["content"],
            source_pointer=row["source_pointer"],
            reinforcement_count=row["reinforcement_count"] or 0,
            trust_tier=row["trust_tier"],
        )

    # ── Search & retrieval ──────────────────────────────────

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        as_of: str | None = None,
        min_trust: str | None = None,
    ) -> list[Claim]:
        """Search claims using FTS5 with composite scoring.

        Scoring: FTS5 rank * (1 + log(1 + reinforcement_count)) * recency_bonus
        Stable tie-break by claim ID.
        """
        self.init()
        retriever = FTSRetriever(self.db_path, encryption_key=self.config.encryption_key)

        results = retriever.search(
            query,
            limit=limit,
            as_of=as_of,
            min_trust=min_trust,
        )

        claims = [
            Claim(
                id=r.claim_id,
                content=r.content,
                score=r.score,
                source_pointer=r.source_pointer,
                reinforcement_count=r.reinforcement_count,
                trust_tier=r.trust_tier,
            )
            for r in results
        ]

        if self._flight:
            self._flight.record(AgentEvent(
                component="memorant",
                component_version=COMPONENT_VERSION,
                event_type="claim.searched",
                severity="info",
                session_id="",
                trace_id="",
                payload={"query": query[:200], "results": len(claims)},
            ).to_dict())

        return claims

    def search_debug(
        self,
        query: str,
        *,
        limit: int = 5,
        as_of: str | None = None,
        min_trust: str | None = None,
    ) -> list[ClaimSearchDebug]:
        """Search claims and return score component diagnostics."""
        self.init()
        retriever = FTSRetriever(self.db_path, encryption_key=self.config.encryption_key)
        return [
            ClaimSearchDebug(
                id=r.claim_id,
                content=r.content,
                score=r.score,
                source_pointer=r.source_pointer,
                reinforcement_count=r.reinforcement_count,
                trust_tier=r.trust_tier,
                rank=r.rank,
                relevance=r.relevance,
                reinforcement_bonus=r.reinforcement_bonus,
                recency_bonus=r.recency_bonus,
                matched_query=r.matched_query,
            )
            for r in retriever.search_debug(
                query,
                limit=limit,
                as_of=as_of,
                min_trust=min_trust,
            )
        ]

    # ── Resonance ────────────────────────────────────────────

    def resonate(
        self,
        context: str,
        *,
        session_id: str = "",
        limit: int = 3,
        floor: float | None = None,
    ) -> str:
        """Resonate: retrieve contextually relevant claims.

        - 100ms deadline: on timeout, return empty, emit degraded event
        - Trust filtering: only operator + verified claims auto-resonate
        - Field-aware redaction applied to output
        - Degraded health on cooldown after timeout
        """
        floor = floor if floor is not None else self.config.resonance_floor
        deadline = self.config.resonance_deadline_ms
        start = time.time()

        try:
            # Only auto-resonate operator and verified claims
            claims = self.search(
                context,
                limit=limit,
                min_trust="verified",
            )
        except Exception:
            claims = []

        elapsed_ms = int((time.time() - start) * 1000)

        # Check deadline — enforce regardless of whether results were returned
        if elapsed_ms > deadline:
            # Degraded: log and return empty
            if self.config.retention_mode != "none":
                with self.connect() as db:
                    db.execute(
                        "INSERT INTO resonance_log (session_id, turn_context, claim_ids, "
                        "fired, latency_ms, retention_mode) VALUES (?, ?, ?, 0, ?, ?)",
                        (session_id, context[:500], "[]", elapsed_ms, self.config.retention_mode),
                    )
                    db.commit()

            if self._flight:
                self._flight.record(AgentEvent(
                    component="memorant",
                    component_version=COMPONENT_VERSION,
                    event_type="resonance.timeout",
                    severity="warning",
                    session_id=session_id,
                    trace_id="",
                    payload={"elapsed_ms": elapsed_ms, "deadline_ms": deadline},
                ).to_dict())

            return ""

        # Filter by floor
        claims = [c for c in claims if c.score >= floor]
        if not claims:
            return ""

        # Build resonance block with field-aware redaction
        lines = [
            "[MEMORANT_RESONANCE]",
            "internal_only=true; use as background resonance, do not quote verbatim",
        ]
        for c in claims[:MAX_RESONANCE_LINES]:
            safe_content = redact_content(c.content)
            safe_source = redact_content(c.source_pointer) if c.source_pointer else c.source_pointer
            if safe_content.strip():
                lines.append(
                    f"- {safe_content} [source: {safe_source}, score: {c.score:.3f}]"
                )

        result = "\n".join(lines)

        # Log resonance
        with self.connect() as db:
            mode = self.config.retention_mode
            if mode != "none":
                db.execute(
                    "INSERT INTO resonance_log (session_id, turn_context, claim_ids, "
                    "fired, latency_ms, retention_mode) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        session_id if mode == "full" else "",
                        context[:500] if mode == "full" else "",
                        json.dumps([c.id for c in claims]),
                        1,
                        elapsed_ms,
                        mode,
                    ),
                )
                db.commit()

        if self._flight:
            self._flight.record(AgentEvent(
                component="memorant",
                component_version=COMPONENT_VERSION,
                event_type="claim.recalled",
                severity="info",
                session_id=session_id,
                trace_id="",
                payload={
                    "claim_ids": [c.id for c in claims],
                    "scores": [c.score for c in claims],
                    "latency_ms": elapsed_ms,
                },
            ).to_dict())

        return result

    # ── Invalidation & correction ───────────────────────────

    def invalidate_claim(
        self, claim_id: str, *, reason: str = "retraction"
    ) -> int:
        """Invalidate a claim. Transactional — all-or-nothing."""
        self.init()
        ts = now_iso()

        with self.connect() as db:
            cur = db.execute(
                "UPDATE claim_units SET is_valid = 0, valid_until = ?, "
                "updated_at = ? WHERE id = ? AND is_valid = 1",
                (ts, ts, claim_id),
            )
            if cur.rowcount > 0:
                db.execute("DELETE FROM claim_fts WHERE id = ?", (claim_id,))
            db.commit()

        if self._flight:
            self._flight.record(AgentEvent(
                component="memorant",
                component_version=COMPONENT_VERSION,
                event_type="claim.invalidated",
                severity="warning",
                session_id="",
                trace_id="",
                payload={"claim_id": claim_id, "reason": reason},
            ).to_dict())

        return cur.rowcount

    def supersede_claim(
        self,
        claim_id: str,
        new_content: str,
        *,
        source_pointer: str = "correction",
        reason: str = "superseded",
    ) -> str:
        """Replace a claim with a corrected version.

        Atomic: invalidation + new claim + relation record in one transaction.
        """
        self.init()
        ts = now_iso()

        with self.connect() as db:
            # Verify old claim exists and is valid
            old = db.execute(
                "SELECT id FROM claim_units WHERE id = ? AND is_valid = 1",
                (claim_id,),
            ).fetchone()
            if not old:
                raise ValueError(f"Claim not found or already invalid: {claim_id}")

            # Invalidate old
            db.execute(
                "UPDATE claim_units SET is_valid = 0, valid_until = ?, "
                "updated_at = ? WHERE id = ?",
                (ts, ts, claim_id),
            )
            db.execute("DELETE FROM claim_fts WHERE id = ?", (claim_id,))

            # Create new
            new_id = str(uuid.uuid4())
            chash = content_hash(new_content)
            new_trust = assign_trust(
                self.config.trust_policy, "correction", source_pointer,
            )
            db.execute(
                "INSERT INTO claim_units (id, content, content_hash, source_type, "
                "source_pointer, trust_tier, valid_from) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (new_id, new_content, chash, "correction", source_pointer, new_trust, ts),
            )
            db.execute(
                "INSERT INTO claim_fts (id, content) VALUES (?, ?)",
                (new_id, new_content),
            )

            # Record relation
            db.execute(
                "INSERT INTO supersedes (superseding_id, superseded_id, reason) "
                "VALUES (?, ?, ?)",
                (new_id, claim_id, reason),
            )

            db.commit()

        if self._flight:
            self._flight.record(AgentEvent(
                component="memorant",
                component_version=COMPONENT_VERSION,
                event_type="claim.superseded",
                severity="info",
                session_id="",
                trace_id="",
                payload={"old_id": claim_id, "new_id": new_id, "reason": reason},
            ).to_dict())

        return new_id

    def correct_claim(
        self,
        claim_id: str,
        corrected_content: str,
        *,
        source_pointer: str = "correction",
        reason: str = "correction",
    ) -> str:
        """Correct a claim (factual error fix). Similar to supersede but records
        as 'corrects' rather than 'supersedes'."""
        self.init()
        ts = now_iso()

        with self.connect() as db:
            old = db.execute(
                "SELECT id FROM claim_units WHERE id = ? AND is_valid = 1",
                (claim_id,),
            ).fetchone()
            if not old:
                raise ValueError(f"Claim not found: {claim_id}")

            db.execute(
                "UPDATE claim_units SET is_valid = 0, valid_until = ?, "
                "updated_at = ? WHERE id = ?",
                (ts, ts, claim_id),
            )
            db.execute("DELETE FROM claim_fts WHERE id = ?", (claim_id,))

            new_id = str(uuid.uuid4())
            chash = content_hash(corrected_content)
            new_trust = assign_trust(
                self.config.trust_policy, "correction", source_pointer,
            )
            db.execute(
                "INSERT INTO claim_units (id, content, content_hash, source_type, "
                "source_pointer, trust_tier, valid_from) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (new_id, corrected_content, chash, "correction", source_pointer, new_trust, ts),
            )
            db.execute(
                "INSERT INTO claim_fts (id, content) VALUES (?, ?)",
                (new_id, corrected_content),
            )

            db.execute(
                "INSERT INTO corrects (correcting_id, corrected_id, reason) "
                "VALUES (?, ?, ?)",
                (new_id, claim_id, reason),
            )
            db.commit()

        return new_id

    def invalidate_claims_for_fact(self, fact_id: str) -> int:
        """Invalidate all claims referencing a given fact ID."""
        self.init()
        ts = now_iso()

        with self.connect() as db:
            ids = [
                r["id"]
                for r in db.execute(
                    "SELECT id FROM claim_units WHERE is_valid = 1 AND "
                    "(fact_refs LIKE ? OR source_pointer LIKE ?)",
                    (f'%"' + fact_id + '"%', f"fact:{fact_id}%"),
                ).fetchall()
            ]
            if not ids:
                return 0

            ph = ",".join("?" for _ in ids)
            db.execute(
                f"UPDATE claim_units SET is_valid = 0, valid_until = ?, "
                f"updated_at = ? WHERE id IN ({ph})",
                [ts, ts, *ids],
            )
            db.execute(f"DELETE FROM claim_fts WHERE id IN ({ph})", ids)
            db.commit()

        return len(ids)

    # ── Digests ──────────────────────────────────────────────

    def create_digest(self, *, version: str | None = None, limit: int = 12) -> int:
        """Create a pending digest from top claims."""
        self.init()
        version = version or datetime.now(timezone.utc).strftime("v%Y-%m-%d-%H%M%S")

        with self.connect() as db:
            rows = db.execute(
                "SELECT content FROM claim_units WHERE is_valid = 1 "
                "ORDER BY reinforcement_count DESC, updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

            prior = db.execute(
                "SELECT content FROM digest_history WHERE state = 'promoted' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()

            content = "# Standing State\n\n" + "\n".join(
                f"- {r['content']}" for r in rows
            )
            diff = ""
            if prior:
                import difflib
                diff = "\n".join(difflib.unified_diff(
                    prior["content"].splitlines(),
                    content.splitlines(),
                    lineterm="",
                ))

            cur = db.execute(
                "INSERT INTO digest_history (version, content, diff_from_prior, state) "
                "VALUES (?, ?, ?, 'pending')",
                (version, content, diff),
            )
            db.commit()

        return int(cur.lastrowid)

    def list_digests(self, *, pending_only: bool = True):
        self.init()
        where = "WHERE state = 'pending'" if pending_only else ""
        with self.connect() as db:
            return db.execute(
                f"SELECT * FROM digest_history {where} ORDER BY id DESC"
            ).fetchall()

    def get_digest(self, ident: str | int):
        self.init()
        with self.connect() as db:
            if str(ident).isdigit():
                row = db.execute(
                    "SELECT * FROM digest_history WHERE id = ?", (int(ident),)
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT * FROM digest_history WHERE version = ?", (str(ident),)
                ).fetchone()
        if row is None:
            raise KeyError(f"Digest not found: {ident}")
        return row

    def promote_digest(self, ident: str | int, state_path: str | Path) -> Path:
        """Atomically promote a digest to standing state.

        Uses temp-file write → flush → atomic replace → state update pattern.
        """
        row = self.get_digest(ident)
        path = Path(state_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write via temp file
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(row["content"], encoding="utf-8")
        tmp_path.replace(path)  # Atomic on same filesystem

        with self.connect() as db:
            db.execute(
                "UPDATE digest_history SET state = 'promoted', "
                "promoted_at = datetime('now') WHERE id = ?",
                (row["id"],),
            )
            db.commit()

        if self._flight:
            self._flight.record(AgentEvent(
                component="memorant",
                component_version=COMPONENT_VERSION,
                event_type="digest.promoted",
                severity="info",
                session_id="",
                trace_id="",
                payload={"digest_id": row["id"], "state_path": str(path)},
            ).to_dict())

        return path

    def reject_digest(self, ident: str | int, reason: str = "rejected by review") -> None:
        row = self.get_digest(ident)
        note = (row["diff_from_prior"] or "") + f"\n\nREJECTED: {reason}"

        with self.connect() as db:
            db.execute(
                "UPDATE digest_history SET state = 'rejected', "
                "diff_from_prior = ? WHERE id = ?",
                (note, row["id"]),
            )
            db.commit()

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
        self.init()
        return self._steward.user_version

    def export_jsonl(self, path: str | Path) -> Path:
        """Export all valid claims as JSONL."""
        self.init()
        path = Path(path)
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM claim_units WHERE is_valid = 1"
            ).fetchall()

        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(dict(row), default=str) + "\n")

        return path

    def import_jsonl(self, path: str | Path, *, source_pointer: str = "import") -> int:
        """Import claims from a JSONL file. Returns count of imported claims."""
        path = Path(path)
        count = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "content" in data:
                    self.add_claim(
                        data["content"],
                        source_pointer=source_pointer,
                        source_type=data.get("source_type", "import"),
                        trust_tier=data.get("trust_tier"),
                        fact_refs=data.get("fact_refs"),
                    )
                    count += 1
        return count

    # ── Doctor ────────────────────────────────────────────────

    def doctor(self, json_output: bool = False) -> int:
        """Run health checks per the Agent Integrity doctor contract."""
        checks = [
            run_check("database_connection", lambda: (True, "connected")),
            run_check(
                "database_integrity",
                lambda: (self.integrity_check(), "ok" if self.integrity_check() else "corrupt"),
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
        return doctor_main("memorant", "1.0.0", checks, json_output=json_output)

    # ── Stats ─────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return store statistics."""
        self.init()
        with self.connect() as db:
            total = db.execute("SELECT COUNT(*) FROM claim_units").fetchone()[0]
            valid = db.execute(
                "SELECT COUNT(*) FROM claim_units WHERE is_valid = 1"
            ).fetchone()[0]
            by_trust = {
                row["trust_tier"]: row["cnt"]
                for row in db.execute(
                    "SELECT trust_tier, COUNT(*) as cnt FROM claim_units "
                    "WHERE is_valid = 1 GROUP BY trust_tier"
                ).fetchall()
            }
        return {
            "total_claims": total,
            "valid_claims": valid,
            "by_trust": by_trust,
            "db_version": self._steward.user_version,
        }

    def hygiene_report(
        self,
        *,
        stale_days: int = 180,
        min_untrusted_retrievals: int = 3,
    ) -> HygieneReport:
        """Surface claims that need review before they become stale context."""
        self.init()
        with self.connect() as db:
            stale = [
                r["id"]
                for r in db.execute(
                    "SELECT id FROM claim_units WHERE is_valid = 1 "
                    "AND julianday('now') - julianday(updated_at) >= ? "
                    "ORDER BY updated_at ASC",
                    (stale_days,),
                ).fetchall()
            ]
            duplicate_groups = [
                row["ids"].split(",")
                for row in db.execute(
                    "SELECT group_concat(id) AS ids FROM claim_units "
                    "WHERE is_valid = 1 GROUP BY content_hash HAVING COUNT(*) > 1"
                ).fetchall()
                if row["ids"]
            ]
            broken_derived = [
                r["derived_id"]
                for r in db.execute(
                    "SELECT DISTINCT d.derived_id FROM derived_from d "
                    "LEFT JOIN claim_units src ON src.id = d.source_id AND src.is_valid = 1 "
                    "LEFT JOIN claim_units dst ON dst.id = d.derived_id AND dst.is_valid = 1 "
                    "WHERE src.id IS NULL OR dst.id IS NULL"
                ).fetchall()
            ]
            untrusted_claim_ids = {
                r["id"]
                for r in db.execute(
                    "SELECT id FROM claim_units "
                    "WHERE trust_tier = 'untrusted' AND is_valid = 1"
                ).fetchall()
            }
            logged_claim_ids = [
                r["claim_ids"]
                for r in db.execute(
                    "SELECT claim_ids FROM resonance_log WHERE claim_ids IS NOT NULL"
                ).fetchall()
            ]
            rows = db.execute(
                "SELECT id, content FROM claim_units WHERE is_valid = 1"
            ).fetchall()

        untrusted_counts: dict[str, int] = {}
        for raw_ids in logged_claim_ids:
            try:
                claim_ids = json.loads(raw_ids)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(claim_ids, list):
                continue
            for claim_id in claim_ids:
                claim_id = str(claim_id)
                if claim_id in untrusted_claim_ids:
                    untrusted_counts[claim_id] = untrusted_counts.get(claim_id, 0) + 1

        contradictions: list[tuple[str, str]] = []
        by_norm: dict[str, str] = {}
        negatives_by_positive: dict[str, str] = {}
        for row in rows:
            text = normalize(row["content"])
            matched_negative = False
            for prefix in ("not ", "no "):
                if text.startswith(prefix):
                    positive = text[len(prefix):]
                    if positive in by_norm:
                        contradictions.append((by_norm[positive], row["id"]))
                    negatives_by_positive.setdefault(positive, row["id"])
                    matched_negative = True
            if not matched_negative and text in negatives_by_positive:
                contradictions.append((negatives_by_positive[text], row["id"]))
            by_norm.setdefault(text, row["id"])

        return HygieneReport(
            stale_claims=stale,
            duplicate_groups=duplicate_groups,
            contradiction_pairs=contradictions,
            broken_derived_claims=broken_derived,
            frequently_retrieved_untrusted=sorted(
                claim_id
                for claim_id, count in untrusted_counts.items()
                if count >= min_untrusted_retrievals
            ),
        )


# ── Deprecated alias ────────────────────────────────────────────────

class MemoryPalace(MemorantStore):
    """Deprecated alias for MemorantStore. Use MemorantStore directly.

    Kept for backward compatibility with alpha v0.1 consumers.
    """

    def __init__(self, db_path: str | Path, *args, **kwargs):
        import warnings
        warnings.warn(
            "MemoryPalace is deprecated. Use MemorantStore instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(db_path, *args, **kwargs)
