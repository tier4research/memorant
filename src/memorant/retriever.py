"""Retrieval protocol and FTS5 implementation for Memorant v1.

Provides:
- Retriever protocol (pluggable retrieval backends)
- FTSRetriever: FTS5-based with composite scoring
- SearchResult dataclass
"""

from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SearchResult:
    """A single retrieval result."""
    claim_id: str
    content: str
    score: float
    source_pointer: str
    reinforcement_count: int
    trust_tier: str
    rank: float  # Raw FTS5 rank


@runtime_checkable
class Retriever(Protocol):
    """Pluggable retrieval backend protocol.

    Implementations must be callable with (query, **kwargs) → list[SearchResult].
    """

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        as_of: str | None = None,
        min_trust: str | None = None,
    ) -> list[SearchResult]:
        """Search claims and return scored results."""
        ...


class FTSRetriever:
    """FTS5-based retriever with composite scoring.

    Scoring formula:
        score = normalized_relevance * reinforcement_bonus * recency_bonus

    Where:
    - normalized_relevance: FTS5 rank normalized to [0,1] within the result set.
      More negative rank → relevance closer to 1.0 (best match = 1.0, worst = 0.0).
    - reinforcement_bonus: 1 + log(1 + reinforcement_count) * 0.3
    - recency_bonus: 1.0 for today, decaying linearly to 0.5 over 90 days

    Tie-break: composite score → reinforcement_count → claim ID.
    """

    def __init__(self, db_path: str | Path, encryption_key: str | None = None):
        self.db_path = Path(db_path)
        self._encryption_key = encryption_key

    def _connect(self) -> sqlite3.Connection:
        if self._encryption_key:
            import sqlcipher3
            db = sqlcipher3.connect(str(self.db_path))
            db.execute(f"PRAGMA key = '{self._encryption_key}'")
        else:
            db = sqlite3.connect(str(self.db_path))
        db.row_factory = sqlite3.Row
        return db

    def _fts_query(self, query: str) -> str:
        """Build a safe FTS5 query string.

        Quotes each term so uppercase FTS5 operators (OR, NOT, NEAR, AND)
        are treated as literal search terms rather than FTS5 syntax.
        """
        terms = [t for t in re.findall(r'\w+', query) if len(t) > 1]
        return " OR ".join(f'"{t}"' for t in terms) if terms else '""'

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        as_of: str | None = None,
        min_trust: str | None = None,
    ) -> list[SearchResult]:
        """Search claims with FTS5 + composite scoring."""
        if not self.db_path.exists():
            return []

        fts_query = self._fts_query(query)
        trust_ranks = {"operator": 0, "verified": 1, "derived": 2, "untrusted": 3}

        with self._connect() as db:
            # Use FTS5 BM25 for ranking with join to claim_units
            sql = """
                SELECT
                    c.id,
                    c.content,
                    c.source_pointer,
                    c.reinforcement_count,
                    c.trust_tier,
                    c.updated_at,
                    f.rank AS fts_rank
                FROM claim_fts f
                JOIN claim_units c ON f.id = c.id
                WHERE claim_fts MATCH ?
                  AND c.is_valid = 1
            """

            params: list = [fts_query]

            # Temporal filter
            if as_of:
                sql += " AND (c.valid_from IS NULL OR c.valid_from <= ?)"
                sql += " AND (c.valid_until IS NULL OR c.valid_until > ?)"
                params.extend([as_of, as_of])

            # Trust filter
            if min_trust:
                sql += " AND c.trust_tier IN ("
                allowed = [
                    t for t in trust_ranks
                    if trust_ranks[t] <= trust_ranks.get(min_trust, 3)
                ]
                sql += ",".join("?" for _ in allowed) + ")"
                params.extend(allowed)

            sql += " ORDER BY f.rank LIMIT 50"

            rows = db.execute(sql, params).fetchall()

        if not rows:
            return []

        # Compute composite scores with result-set-relative normalization.
        # FTS5 rank: negative values, more negative = better match.
        # Since rank magnitude varies with corpus size, we normalize within
        # the result set to [0, 1] so the resonance floor works consistently.
        now = datetime.now(timezone.utc)

        ranks = [r["fts_rank"] for r in rows]
        if ranks:
            min_rank = min(ranks)  # most negative = best match
            max_rank = max(ranks)  # least negative = worst match
            rank_range = max_rank - min_rank
        else:
            min_rank = max_rank = 0.0
            rank_range = 0.0

        results = []
        for r in rows:
            rank_value = float(r["fts_rank"] or 0.0)

            # Normalize to [0, 1]: best match → 1.0, worst match → 0.0
            if rank_range > 0:
                relevance = (rank_value - max_rank) / (min_rank - max_rank)
            else:
                relevance = 1.0  # Only one result or all identical ranks

            # Reinforcement bonus: log scaling, rewards reinforced claims
            reinf = r["reinforcement_count"] or 0
            reinf_bonus = 1 + math.log(1 + reinf) * 0.3

            # Recency bonus: 1.0 for today, decays to 0.5 over 90 days
            updated_at = r["updated_at"]
            if updated_at:
                try:
                    updated = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
                    days_ago = max(0, (now - updated).days)
                    recency_bonus = 1.0 - (0.5 * min(days_ago / 90.0, 1.0))
                except (ValueError, TypeError):
                    recency_bonus = 1.0
            else:
                recency_bonus = 1.0

            # Composite score
            score = relevance * reinf_bonus * recency_bonus

            results.append(SearchResult(
                claim_id=r["id"],
                content=r["content"],
                score=score,
                source_pointer=r["source_pointer"],
                reinforcement_count=reinf,
                trust_tier=r["trust_tier"],
                rank=r["fts_rank"],
            ))

        # Sort by composite score, then reinforcement, then ID (stable tie-break)
        results.sort(
            key=lambda x: (-x.score, -x.reinforcement_count, x.claim_id),
        )

        return results[:limit]
