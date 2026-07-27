"""Contradiction detection for ontology relations.

Only functional_relations trigger review (F2).
Multi-valued relations (e.g., "uses") do not trigger contradiction review.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import OntologyConfig
    from .store import OntologyStore


def check_contradictions(
    store: "OntologyStore",
    source_entity_id: str,
    relation: str,
    target_entity_id: str,
    config: "OntologyConfig",
) -> bool:
    """Check if a new relation contradicts existing functional relations.

    Only functional_relations trigger review (F2).
    For functional relations (e.g., located_in, is_a), if the source entity
    already has a different target for the same relation, a review row is created.

    Returns True if a contradiction review was created.
    """
    if relation not in config.functional_relations:
        return False

    with store.connect() as db:
        # Find existing valid relations with same source and relation type
        existing = db.execute(
            """
            SELECT id, target_entity_id FROM memorant_ontology_relations
             WHERE source_entity_id = ? AND relation = ? AND is_valid = 1
               AND target_entity_id != ?
            """,
            (source_entity_id, relation, target_entity_id),
        ).fetchall()

        if not existing:
            return False

        # Create review entries for each contradiction
        for row in existing:
            db.execute(
                """
                INSERT INTO memorant_ontology_review (relation_id_1, relation_id_2, reason)
                VALUES (?, ?, ?)
                """,
                (
                    row["id"],
                    # We don't have the new relation's ID yet (it may not be inserted),
                    # so we store a synthetic reference
                    f"pending:{source_entity_id}:{relation}:{target_entity_id}",
                    f"Functional contradiction: {source_entity_id} --{relation}--> "
                    f"{target_entity_id} conflicts with existing target {row['target_entity_id']}",
                ),
            )
        db.commit()

    return True


def check_contradictions_for_relation(
    store: "OntologyStore",
    relation_id_1: str,
    relation_id_2: str,
    config: "OntologyConfig",
) -> bool:
    """Check two specific relations for contradiction.

    Used when both relation IDs already exist in the DB.
    Only functional relations are checked.
    """
    with store.connect() as db:
        r1 = db.execute(
            "SELECT * FROM memorant_ontology_relations WHERE id = ?",
            (relation_id_1,),
        ).fetchone()
        r2 = db.execute(
            "SELECT * FROM memorant_ontology_relations WHERE id = ?",
            (relation_id_2,),
        ).fetchone()

        if not r1 or not r2:
            return False

        # Both must be functional relations
        if r1["relation"] not in config.functional_relations:
            return False

        # Same source, same relation, different target
        if (r1["source_entity_id"] == r2["source_entity_id"]
                and r1["relation"] == r2["relation"]
                and r1["target_entity_id"] != r2["target_entity_id"]):

            # Check if review already exists
            existing = db.execute(
                """
                SELECT 1 FROM memorant_ontology_review
                 WHERE (relation_id_1 = ? AND relation_id_2 = ?)
                    OR (relation_id_1 = ? AND relation_id_2 = ?)
                """,
                (relation_id_1, relation_id_2, relation_id_2, relation_id_1),
            ).fetchone()

            if existing:
                return False

            db.execute(
                """
                INSERT INTO memorant_ontology_review (relation_id_1, relation_id_2, reason)
                VALUES (?, ?, ?)
                """,
                (
                    relation_id_1,
                    relation_id_2,
                    f"Functional contradiction: {r1['relation']} has conflicting targets",
                ),
            )
            db.commit()
            return True

    return False
