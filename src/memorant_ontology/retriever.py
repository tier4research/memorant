"""Ontology retriever — graph walks over entities and relations.

Takes OntologyStore (not db_path) to reuse store.connect() PRAGMAs (M2).
All queries include AND is_valid = 1 (M6).
Hard LIMIT 50 on all queries. Depth hard-capped at 3.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .normalizer import normalize_name

if TYPE_CHECKING:
    from .store import OntologyStore


class OntologyRetriever:
    """Graph-based retriever for ontology entities and relations.

    Takes OntologyStore (not a db_path) — share store's connect() PRAGMAs.
    """

    def __init__(self, store: "OntologyStore"):
        self.store = store
        self.config = store.config

    def find_entities(self, name: str, limit: int = 50) -> list[dict]:
        """Normalize the query like stored names (m7).

        Returns list of entity dicts matching the normalized name.
        """
        nn = normalize_name(name)
        with self.store.connect() as db:
            rows = db.execute(
                """
                SELECT * FROM memorant_ontology_entities
                 WHERE name_normalized = ? COLLATE NOCASE
                   AND is_valid = 1
                 LIMIT ?
                """,
                (nn, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def find_related(
        self,
        start_name: str,
        depth: int = 1,
        limit: int = 50,
        with_provenance: bool = False,
    ) -> list[dict]:
        """Hard LIMIT 50. AND is_valid = 1 always. Depth hard-capped at 3."""
        depth = min(depth, 3)
        nn = normalize_name(start_name)

        with self.store.connect() as db:
            if with_provenance:
                sql = """
                    WITH RECURSIVE walk(source_id, relation, target_id, hops, source_claim_id, path) AS (
                        SELECT r.source_entity_id, r.relation, r.target_entity_id, 0, r.source_claim_id,
                               r.source_entity_id || ',' || r.target_entity_id
                          FROM memorant_ontology_relations r
                          JOIN memorant_ontology_entities e ON r.source_entity_id = e.id
                         WHERE e.name_normalized = ? COLLATE NOCASE
                           AND e.is_valid = 1 AND r.is_valid = 1
                        UNION ALL
                        SELECT r.source_entity_id, r.relation, r.target_entity_id,
                               w.hops + 1, r.source_claim_id,
                               w.path || ',' || r.target_entity_id
                          FROM memorant_ontology_relations r
                          JOIN walk w ON r.source_entity_id = w.target_id
                         WHERE r.is_valid = 1 AND w.hops + 1 <= ?
                           AND INSTR(',' || w.path || ',', ',' || r.target_entity_id || ',') = 0
                    )
                    SELECT w.source_id, w.relation, w.target_id, w.hops, w.source_claim_id,
                           se.name AS source_name, te.name AS target_name
                      FROM walk w
                      JOIN memorant_ontology_entities se ON w.source_id = se.id AND se.is_valid = 1
                      JOIN memorant_ontology_entities te ON w.target_id = te.id AND te.is_valid = 1
                     LIMIT ?
                """
            else:
                sql = """
                    WITH RECURSIVE walk(source_id, relation, target_id, hops, path) AS (
                        SELECT r.source_entity_id, r.relation, r.target_entity_id, 0,
                               r.source_entity_id || ',' || r.target_entity_id
                          FROM memorant_ontology_relations r
                          JOIN memorant_ontology_entities e ON r.source_entity_id = e.id
                         WHERE e.name_normalized = ? COLLATE NOCASE
                           AND e.is_valid = 1 AND r.is_valid = 1
                        UNION ALL
                        SELECT r.source_entity_id, r.relation, r.target_entity_id, w.hops + 1,
                               w.path || ',' || r.target_entity_id
                          FROM memorant_ontology_relations r
                          JOIN walk w ON r.source_entity_id = w.target_id
                         WHERE r.is_valid = 1 AND w.hops + 1 <= ?
                           AND INSTR(',' || w.path || ',', ',' || r.target_entity_id || ',') = 0
                    )
                    SELECT w.source_id, w.relation, w.target_id, w.hops,
                           se.name AS source_name, te.name AS target_name
                      FROM walk w
                      JOIN memorant_ontology_entities se ON w.source_id = se.id AND se.is_valid = 1
                      JOIN memorant_ontology_entities te ON w.target_id = te.id AND te.is_valid = 1
                     LIMIT ?
                """
            rows = db.execute(sql, (nn, depth, limit)).fetchall()
        # Deduplicate by edge to guard against distinct paths to the same edge
        seen = set()
        unique_rows = []
        for r in rows:
            key = (r["source_id"], r["target_id"], r["relation"])
            if key not in seen:
                seen.add(key)
                unique_rows.append(r)
        return [dict(r) for r in unique_rows]
