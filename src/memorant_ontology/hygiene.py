"""Hygiene operations for ontology — health checks, rollback, purge.

F5: FK-safe purge — relations first, then entities; trust_history survives.
F18: Escalation debounce via last_alert_at in memorant_ontology_meta.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import OntologyStore


def health(store: "OntologyStore") -> dict:
    """Return ontology health report as a dict."""
    report = {
        "status": "ok",
        "schema_initialized": False,
        "entities": 0,
        "relations": 0,
        "queue_pending": 0,
        "queue_in_progress": 0,
        "queue_complete": 0,
        "queue_failed": 0,
        "reviews_pending": 0,
        "total_cost_usd": 0.0,
        "daily_cost_usd": 0.0,
    }

    try:
        with store.connect() as db:
            # Check if ontology tables exist
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}

            if "memorant_ontology_entities" not in tables:
                report["status"] = "not_initialized"
                return report

            report["schema_initialized"] = True

            report["entities"] = db.execute(
                "SELECT COUNT(*) FROM memorant_ontology_entities WHERE is_valid = 1"
            ).fetchone()[0]

            report["relations"] = db.execute(
                "SELECT COUNT(*) FROM memorant_ontology_relations WHERE is_valid = 1"
            ).fetchone()[0]

            report["queue_pending"] = db.execute(
                "SELECT COUNT(*) FROM memorant_ontology_queue WHERE state = 'pending'"
            ).fetchone()[0]

            report["queue_in_progress"] = db.execute(
                "SELECT COUNT(*) FROM memorant_ontology_queue WHERE state = 'in_progress'"
            ).fetchone()[0]

            report["queue_complete"] = db.execute(
                "SELECT COUNT(*) FROM memorant_ontology_queue WHERE state = 'complete'"
            ).fetchone()[0]

            report["queue_failed"] = db.execute(
                "SELECT COUNT(*) FROM memorant_ontology_queue WHERE state = 'failed'"
            ).fetchone()[0]

            if "memorant_ontology_review" in tables:
                report["reviews_pending"] = db.execute(
                    "SELECT COUNT(*) FROM memorant_ontology_review WHERE status = 'pending'"
                ).fetchone()[0]

            report["total_cost_usd"] = float(db.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM memorant_ontology_queue"
            ).fetchone()[0])

            report["daily_cost_usd"] = float(db.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM memorant_ontology_queue "
                "WHERE completed_at > datetime('now', '-24 hours')"
            ).fetchone()[0])

    except Exception as exc:
        report["status"] = "error"
        report["error"] = repr(exc)

    return report


def rollback(store: "OntologyStore") -> None:
    """Drop all ontology tables. claim_units extension columns remain (F10).

    This is a destructive operation — use with caution.
    """
    tables = [
        "memorant_ontology_review",
        "memorant_ontology_trust_history",
        "memorant_ontology_queue",
        "memorant_ontology_relations",
        "memorant_ontology_entities",
        "memorant_ontology_meta",
    ]
    indexes = [
        "idx_claim_ontology_unprocessed",
        "idx_ontology_relations_source_target",
        "idx_ontology_relations_target",
        "idx_ontology_relations_claim",
        "idx_ontology_queue_state",
        "ux_ontology_queue_active",
    ]

    with store.connect() as db:
        # Drop indexes first
        for idx in indexes:
            db.execute(f"DROP INDEX IF EXISTS {idx}")
        # Drop tables in reverse dependency order
        for table in tables:
            db.execute(f"DROP TABLE IF EXISTS {table}")
        db.commit()


def purge_invalid(store: "OntologyStore", older_than_days: int = 30) -> int:
    """Purge invalidated entities and relations older than N days.

    F5: FK-safe purge — relations first, then entities.
    trust_history survives (NO FK to entities).

    Returns total count of purged rows.
    """
    total = 0

    with store.connect() as db:
        # 1. Purge invalid relations first (they reference entities via FK)
        cur = db.execute(
            """
            DELETE FROM memorant_ontology_relations
             WHERE is_valid = 0
               AND invalidated_at < datetime('now', ? || ' days')
            """,
            (f"-{older_than_days}",),
        )
        total += cur.rowcount

        # 2. Purge invalid entities with NO remaining valid relations (F5)
        # First find entities that have no valid relations (as source or target)
        candidates = db.execute(
            """
            SELECT e.id FROM memorant_ontology_entities e
             WHERE e.is_valid = 0
               AND e.invalidated_at < datetime('now', ? || ' days')
               AND NOT EXISTS (
                   SELECT 1 FROM memorant_ontology_relations r
                    WHERE (r.source_entity_id = e.id OR r.target_entity_id = e.id)
                      AND r.is_valid = 1
               )
            """,
            (f"-{older_than_days}",),
        ).fetchall()

        for row in candidates:
            # trust_history has NO FK to entities, so it survives (F5, W5)
            db.execute(
                "DELETE FROM memorant_ontology_entities WHERE id = ?",
                (row["id"],),
            )
            total += 1

        # 3. Update last_purge_at
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT OR REPLACE INTO memorant_ontology_meta (key, value) VALUES ('last_purge_at', ?)",
            (now,),
        )
        db.commit()

    return total
