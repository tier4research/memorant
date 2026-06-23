#!/usr/bin/env python3
"""Migrate Palace V2 facts.db → Memorant memorant.db

Run inside the VPS container with Memorant installed.
Source: /opt/data/memory_palace_v2/facts.db (Palace V2, 219 facts)
Target: /opt/data/memory_palace_v2/memorant.db (Memorant v1)

Usage:
  /opt/hermes/.venv/bin/python3 migrate_palace_v2.py
  /opt/hermes/.venv/bin/python3 migrate_palace_v2.py --dry-run
"""

import json
import sqlite3
import hashlib
import uuid
import sys
import re
from datetime import datetime, timezone


SOURCE = "/opt/data/memory_palace_v2/facts.db"
TARGET = "/opt/data/memory_palace_v2/memorant.db"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(text: str) -> str:
    return hashlib.sha256(
        re.sub(r"\s+", " ", text.strip().lower()).encode()
    ).hexdigest()


def trust_tier(confidence: float) -> str:
    if confidence >= 0.85:
        return "verified"
    if confidence >= 0.3:
        return "derived"
    return "untrusted"


def sanitize_fts(text: str) -> str:
    """Remove FTS5 special characters from query text."""
    return re.sub(r'[!"#$%&\'()*+,\-./:;<=>?@\[\\\]^_`{|}~]', ' ', text).strip()


def migrate(dry_run: bool = False) -> dict:
    """Run the migration. Returns stats dict."""

    # ── Open source (Palace V2) ──────────────────────────────
    src = sqlite3.connect(SOURCE)
    src.row_factory = sqlite3.Row

    # Count source facts
    fact_count = src.execute(
        "SELECT COUNT(*) FROM facts WHERE is_active = 1"
    ).fetchone()[0]
    quarantine_count = src.execute(
        "SELECT COUNT(*) FROM quarantine"
    ).fetchone()[0]

    stats = {
        "source_facts": fact_count,
        "source_quarantine": quarantine_count,
        "migrated_facts": 0,
        "migrated_quarantine": 0,
        "duplicates_skipped": 0,
        "errors": 0,
        "dry_run": dry_run,
    }

    if dry_run:
        print(f"DRY RUN — would migrate {fact_count} facts + {quarantine_count} quarantine")
        src.close()
        return stats

    # ── Create target (Memorant) ─────────────────────────────
    dst = sqlite3.connect(TARGET)
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA foreign_keys=ON")

    # Create Memorant schema
    from memorant.schema import SCHEMA_V1

    for table_name, sql in SCHEMA_V1.items():
        try:
            dst.execute(sql)
        except sqlite3.OperationalError as e:
            print(f"  [skip] {table_name}: {e}")

    # ── Migrate facts → claim_units ──────────────────────────
    facts = src.execute(
        "SELECT * FROM facts WHERE is_active = 1 ORDER BY created_at"
    ).fetchall()

    for f in facts:
        cid = str(uuid.uuid4())
        chash = content_hash(f["content"] or "")
        if not chash:
            stats["errors"] += 1
            continue

        tier = trust_tier(f["confidence"] if f["confidence"] is not None else 0.5)

        # Store Palace V2 metadata in fact_refs JSON
        fact_refs = json.dumps({
            "room": f["room_id"] or "",
            "entity": f["entity_id"] or "",
            "category": f["category"] or "",
            "source": f["source_doc_id"] or "",
            "provenance": f["provenance_group"] or "",
            "palace_v2_fact_id": f["id"] or "",
        })

        valid_from = f["valid_from"] or now_iso()

        try:
            dst.execute(
                """INSERT INTO claim_units (
                    id, content, content_hash, fact_refs,
                    source_type, source_pointer, trust_tier,
                    valid_from, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cid,
                    f["content"],
                    chash,
                    fact_refs,
                    "migration",
                    f"palace-v2:{f['source_doc_id'] or 'unknown'}",
                    tier,
                    valid_from,
                    now_iso(),
                    now_iso(),
                ),
            )
            dst.execute(
                "INSERT INTO claim_fts (id, content) VALUES (?, ?)",
                (cid, sanitize_fts(f["content"] or "")),
            )
            stats["migrated_facts"] += 1
        except sqlite3.IntegrityError:
            # Duplicate content_hash — increment reinforcement on existing
            existing = dst.execute(
                "SELECT id FROM claim_units WHERE content_hash = ?", (chash,)
            ).fetchone()
            if existing:
                dst.execute(
                    "UPDATE claim_units SET reinforcement_count = reinforcement_count + 1, "
                    "last_touched = datetime('now') WHERE id = ?",
                    (existing["id"],),
                )
                stats["duplicates_skipped"] += 1
            else:
                stats["errors"] += 1

    # ── Migrate quarantine → claim_units (untrusted) ─────────
    quars = src.execute("SELECT * FROM quarantine").fetchall()
    for q in quars:
        content = q["content"] if "content" in q.keys() else str(q)
        chash = content_hash(content)
        if not chash:
            stats["errors"] += 1
            continue

        cid = str(uuid.uuid4())
        try:
            dst.execute(
                """INSERT INTO claim_units (
                    id, content, content_hash, fact_refs,
                    source_type, source_pointer, trust_tier,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cid,
                    content,
                    chash,
                    json.dumps({"quarantine": True, "source": "palace-v2"}),
                    "migration",
                    "palace-v2:quarantine",
                    "untrusted",
                    now_iso(),
                    now_iso(),
                ),
            )
            dst.execute(
                "INSERT INTO claim_fts (id, content) VALUES (?, ?)",
                (cid, sanitize_fts(content)),
            )
            stats["migrated_quarantine"] += 1
        except sqlite3.IntegrityError:
            stats["duplicates_skipped"] += 1

    # ── Finalize ─────────────────────────────────────────────
    dst.commit()

    # Set schema version
    from memorant.schema import MIGRATIONS

    max_version = max(MIGRATIONS) if MIGRATIONS else 0
    dst.execute(f"PRAGMA user_version = {max_version}")

    total = dst.execute("SELECT COUNT(*) FROM claim_units").fetchone()[0]
    fts_count = dst.execute("SELECT COUNT(*) FROM claim_fts").fetchone()[0]

    # WAL checkpoint
    dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    stats["total_claim_units"] = total
    stats["fts_count"] = fts_count

    # Integrity check
    integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
    stats["integrity"] = integrity

    src.close()
    dst.close()

    return stats


def main():
    dry_run = "--dry-run" in sys.argv

    print(f"Palace V2 → Memorant migration")
    print(f"  Source: {SOURCE}")
    print(f"  Target: {TARGET}")
    print(f"  Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print()

    try:
        stats = migrate(dry_run=dry_run)
    except ImportError as e:
        print(f"ERROR: Memorant not installed — {e}")
        print("Install with: /opt/hermes/.venv/bin/pip install memorant")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print()
    print("Migration complete:")
    print(f"  Facts migrated:    {stats['migrated_facts']}")
    print(f"  Quarantine:        {stats['migrated_quarantine']}")
    print(f"  Duplicates skipped:{stats['duplicates_skipped']}")
    print(f"  Errors:            {stats['errors']}")
    print(f"  Total claim_units: {stats.get('total_claim_units', '?')}")
    print(f"  FTS entries:       {stats.get('fts_count', '?')}")
    print(f"  Integrity:         {stats.get('integrity', '?')}")

    if stats.get("integrity") != "ok":
        print("WARNING: Integrity check failed!")
        sys.exit(1)

    print("DONE")


if __name__ == "__main__":
    main()
