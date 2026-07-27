"""Queue management for ontology extraction — enqueue, dequeue, lease recovery.

Race-safe dequeue uses rowcount-checked UPDATE...RETURNING (F3, F4).
ux_ontology_queue_active partial unique index prevents duplicate active rows.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import OntologyStore


def enqueue(store: "OntologyStore", claim_id: str) -> bool:
    """Enqueue a claim for extraction. Returns True if a new row was inserted.

    Guards:
    1. Claim already processed (ontology_processed_at IS NOT NULL) → skip
    2. ux_ontology_queue_active prevents duplicate pending/in_progress rows
    """
    with store.connect() as db:
        # Guard 1: claim already processed?
        already = db.execute(
            "SELECT 1 FROM claim_units WHERE id = ? AND ontology_processed_at IS NOT NULL",
            (claim_id,),
        ).fetchone()
        if already:
            return False
        # Guard 2: ux_ontology_queue_active prevents duplicate pending/in_progress rows
        try:
            db.execute(
                "INSERT INTO memorant_ontology_queue (claim_id, state) VALUES (?, 'pending')",
                (claim_id,),
            )
            db.commit()
            return True
        except sqlite3.IntegrityError:
            return False  # already queued


logger = logging.getLogger(__name__)


_MAX_DEQUEUE_RETRIES = 10


def dequeue(store: "OntologyStore", worker_id: str) -> dict | None:
    """Race-safe dequeue: rowcount-checked, loop-on-miss (F4).

    Uses UPDATE...RETURNING with a subquery to atomically claim a pending row.
    If a concurrent worker claims the same row first, we retry on the next
    pending row after refreshing the transaction snapshot.
    Returns the claimed row dict or None if no pending rows.
    """
    with store.connect() as db:
        for _ in range(_MAX_DEQUEUE_RETRIES):
            cur = db.execute(
                """
                UPDATE memorant_ontology_queue
                   SET state = 'in_progress', started_at = datetime('now'), worker_id = ?
                 WHERE id = (SELECT id FROM memorant_ontology_queue
                              WHERE state = 'pending' ORDER BY enqueued_at LIMIT 1)
                   AND state = 'pending'
                RETURNING id, claim_id, prompt_version
                """,
                (worker_id,),
            )
            row = cur.fetchone()
            if row:
                db.commit()
                return dict(row)

            # No row claimed: either the queue is empty or we lost a race.
            # Refresh the snapshot and check for pending rows.
            db.rollback()
            time.sleep(0.01)
            any_pending = db.execute(
                "SELECT 1 FROM memorant_ontology_queue WHERE state = 'pending' LIMIT 1"
            ).fetchone()
            if not any_pending:
                return None
            # Otherwise loop and try the next pending row.
        return None


def lease_recover(store: "OntologyStore", lease_seconds: int) -> int:
    """Reset stranded in_progress rows at every tick start.

    Returns the number of recovered rows.
    """
    with store.connect() as db:
        cur = db.execute(
            """
            UPDATE memorant_ontology_queue
               SET state = 'pending', worker_id = NULL, attempts = attempts + 1,
                   last_error = 'lease expired (worker died?)'
             WHERE state = 'in_progress'
               AND started_at < datetime('now', ? || ' seconds')
            """,
            (f"-{lease_seconds}",),
        )
        db.commit()
    return cur.rowcount


def complete(store: "OntologyStore", queue_id: int, cost_usd: float = 0.0) -> None:
    """Mark a queue row as complete."""
    with store.connect() as db:
        db.execute(
            "UPDATE memorant_ontology_queue SET state = 'complete', "
            "completed_at = datetime('now'), cost_usd = ? WHERE id = ?",
            (cost_usd, queue_id),
        )
        db.commit()


def fail(
    store: "OntologyStore",
    queue_id: int,
    error: str,
    max_attempts: int = 3,
) -> None:
    """Mark a queue row as failed or reset to pending for retry.

    On attempts >= max_attempts, state='failed'.
    Otherwise, state='pending' with attempts incremented.
    """
    with store.connect() as db:
        row = db.execute(
            "SELECT attempts FROM memorant_ontology_queue WHERE id = ?",
            (queue_id,),
        ).fetchone()
        if not row:
            return
        attempts = (row["attempts"] or 0) + 1
        if attempts >= max_attempts:
            db.execute(
                "UPDATE memorant_ontology_queue SET state = 'failed', "
                "attempts = ?, last_error = ?, completed_at = datetime('now') WHERE id = ?",
                (attempts, error, queue_id),
            )
        else:
            db.execute(
                "UPDATE memorant_ontology_queue SET state = 'pending', "
                "attempts = ?, last_error = ?, worker_id = NULL WHERE id = ?",
                (attempts, error, queue_id),
            )
        db.commit()


def sweep_dead_letter(store: "OntologyStore") -> int:
    """Read dead-letter file and re-enqueue each entry. Returns count re-enqueued.

    Atomically rotates the dead-letter file so concurrent writers append to a
    fresh file while we process the rotated one.
    """
    from pathlib import Path
    import json

    dl = store.db_path.with_suffix(".dead-letter.jsonl")
    if not dl.exists():
        return 0

    # Rotate atomically. On Windows the rotate can fail if another process has
    # the file open; abort the sweep rather than risk data loss by truncating.
    processing = store.db_path.with_suffix(".dead-letter.processing.jsonl")
    try:
        os.replace(dl, processing)
    except OSError:
        # File is locked by another writer. Leave it for the next sweep.
        return 0

    count = 0
    try:
        with processing.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cid = entry.get("claim_id")
                if cid and enqueue(store, cid):
                    count += 1
    except Exception as exc:
        # Processing failed: restore the file so the unprocessed entries can
        # be retried on the next sweep. Return the partial count so callers
        # know some entries were already enqueued.
        logger.warning("Dead-letter sweep failed mid-processing: %s", exc)
        try:
            os.replace(processing, dl)
        except OSError as restore_err:
            logger.error("Failed to restore dead-letter file: %s", restore_err)
            return count
        return count

    try:
        processing.unlink()
    except FileNotFoundError:
        pass
    logger.debug("Dead-letter sweep complete; re-enqueued %d entries", count)
    return count


def check_escalation(store: "OntologyStore", config) -> bool:
    """Check if escalation alert should fire.

    Returns True if:
    - 100+ failed rows in trailing 24h
    - last_alert_at is older than 24h (or empty)
    """
    from datetime import datetime, timedelta, timezone

    with store.connect() as db:
        failed_count = db.execute(
            """
            SELECT COUNT(*) as cnt FROM memorant_ontology_queue
             WHERE state = 'failed'
               AND completed_at > datetime('now', '-24 hours')
            """
        ).fetchone()["cnt"]

        if failed_count < config.alert_failed_threshold:
            return False

        last_alert = db.execute(
            "SELECT value FROM memorant_ontology_meta WHERE key = 'last_alert_at'"
        ).fetchone()

        if last_alert and last_alert["value"]:
            # Check if last alert was within 24h (use timezone-aware datetime)
            try:
                last = datetime.fromisoformat(last_alert["value"])
                if datetime.now(timezone.utc) - last < timedelta(hours=24):
                    return False
            except (ValueError, TypeError):
                pass

        # Update last_alert_at
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT OR REPLACE INTO memorant_ontology_meta (key, value) VALUES ('last_alert_at', ?)",
            (now,),
        )
        db.commit()

    return True
