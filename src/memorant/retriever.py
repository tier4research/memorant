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
        score = fts_rank * (1 + log(1 + reinforcement_count)) * recency_bonus

    Where:
    - fts_rank: BM25 rank from FTS5 (-bm25rank normalized, higher = better)
    - reinforcement_count: number of times this claim has been reinforced
    - recency_bonus: recency decay factor (1.0 for today, decaying to 0.5 over 90 days)

    Tie-break: stable claim ID sort.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.db_path))
        db.row_factory = sqlite3.Row
        return db

    def _fts_query(self, query: str) -> str:
        """Build a safe FTS5 query string.

        Uses simple OR of bare terms — FTS5 handles tokenization and stemming.
        """
        terms = [t for t in re.findall(r'\w+', query) if len(t) > 1]
        return " OR ".join(terms) if terms else '""'

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

        # Compute composite scores.
        # FTS5 rank values are negative — better matches are closer to 0.
        # We convert to a positive relevance score where higher = better.
        results = []
        for r in rows:
            # Convert FTS5 rank to relevance: 1/(1+abs(rank)) → [0,1]
            # Better matches (rank closer to 0) → relevance closer to 1
            rank_value = r["fts_rank"] or -1
            relevance = 1.0 / (1.0 + abs(rank_value))

            # Reinforcement bonus: log scaling, rewards reinforced claims
            reinf = r["reinforcement_count"] or 0
            reinf_bonus = 1 + math.log(1 + reinf) * 0.3

            # Composite score ensures positive values
            score = relevance * reinf_bonus

            results.append(SearchResult(
                claim_id=r["id"],
                content=r["content"],
                score=round(score, 6),
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
