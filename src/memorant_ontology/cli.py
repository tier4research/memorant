"""Memorant Auto-Ontology — CLI entry point.

Usage:
    python -m memorant_ontology --help
    python -m memorant_ontology status
    python -m memorant_ontology worker --db <path> --limit 10 --rpm 30
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="memorant-ontology",
        description="Memorant Auto-Ontology — extract entities and relations from claim units",
    )
    p.add_argument("--db", default="./memorant.db", help="Database path")
    sub = p.add_subparsers(dest="cmd", required=True)

    # status
    sp = sub.add_parser("status", help="Show ontology health status")
    sp.set_defaults(func=cmd_status)

    # find
    sp = sub.add_parser("find", help="Find entities by name")
    sp.add_argument("name", help="Entity name to search")
    sp.add_argument("--depth", type=int, default=1, help="Graph walk depth")
    sp.add_argument("--limit", type=int, default=50, help="Max results")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_find)

    # invalidate
    sp = sub.add_parser("invalidate", help="Invalidate an entity by name")
    sp.add_argument("name", help="Entity name")
    sp.set_defaults(func=cmd_invalidate)

    # merge
    sp = sub.add_parser("merge", help="Merge two entities")
    sp.add_argument("survivor_id", help="Surviving entity ID")
    sp.add_argument("loser_id", help="Entity ID to merge into survivor")
    sp.set_defaults(func=cmd_merge)

    # purge
    sp = sub.add_parser("purge", help="Purge invalidated entities")
    sp.add_argument("--older-than", type=int, default=30, help="Days")
    sp.set_defaults(func=cmd_purge)

    # rollback
    sp = sub.add_parser("rollback", help="Drop all ontology tables")
    sp.set_defaults(func=cmd_rollback)

    # reextract
    sp = sub.add_parser("reextract", help="Re-queue claims for extraction")
    sp.add_argument("--limit", type=int, default=100)
    sp.set_defaults(func=cmd_reextract)

    # sweep-dead-letter
    sp = sub.add_parser("sweep-dead-letter", help="Re-enqueue dead-letter entries")
    sp.set_defaults(func=cmd_sweep_dead_letter)

    # enable / disable
    sp = sub.add_parser("enable", help="Enable ontology for this DB")
    sp.set_defaults(func=cmd_enable)

    sp = sub.add_parser("disable", help="Disable ontology for this DB")
    sp.set_defaults(func=cmd_disable)

    # worker
    sp = sub.add_parser("worker", help="Run ontology extraction worker")
    sp.add_argument("--limit", type=int, default=10, help="Claims per tick")
    sp.add_argument("--rpm", type=int, default=30, help="LLM requests per minute")
    sp.set_defaults(func=cmd_worker)

    # init
    sp = sub.add_parser("init", help="Initialize ontology schema")
    sp.set_defaults(func=cmd_init)

    # import-jsonl
    sp = sub.add_parser("import-jsonl", help="Bulk load synthetic claims")
    sp.add_argument("path", help="JSONL file path")
    sp.set_defaults(func=cmd_import_jsonl)

    return p


def _get_store(db_path: str):
    """Lazy-load store to avoid import overhead for --help."""
    from .store import OntologyStore
    from .config import OntologyConfig
    return OntologyStore(db_path, OntologyConfig())


def cmd_status(args):
    from .hygiene import health
    store = _get_store(args.db)
    report = health(store)
    import json
    print(json.dumps(report, indent=2))


def cmd_find(args):
    from .retriever import OntologyRetriever
    from .normalizer import normalize_name
    store = _get_store(args.db)
    retriever = OntologyRetriever(store)
    entities = retriever.find_entities(args.name, limit=args.limit)
    if not entities:
        print(f"No entities found for '{args.name}'")
        return
    for e in entities:
        print(f"  {e['id']}  {e['entity_type']}  {e['name']}  trust={e['trust_tier']}  valid={e['is_valid']}")
    if args.depth > 0:
        related = retriever.find_related(args.name, depth=args.depth, limit=args.limit)
        if related:
            print(f"\nRelated (depth {args.depth}):")
            for r in related:
                print(f"  {r}")


def cmd_invalidate(args):
    from .store import OntologyStore
    from .config import OntologyConfig
    from .normalizer import normalize_name
    store = _get_store(args.db)
    nn = normalize_name(args.name)
    with store.connect() as db:
        rows = db.execute(
            "SELECT id, name FROM memorant_ontology_entities WHERE name_normalized = ? COLLATE NOCASE AND is_valid = 1",
            (nn,),
        ).fetchall()
        if not rows:
            print(f"No valid entities matching '{args.name}'")
            return
        for r in rows:
            db.execute("UPDATE memorant_ontology_entities SET is_valid = 0, invalidated_at = datetime('now') WHERE id = ?", (r["id"],))
            print(f"Invalidated: {r['id']} ({r['name']})")
        db.commit()


def cmd_merge(args):
    from .store import OntologyStore
    from .config import OntologyConfig
    store = _get_store(args.db)
    store.merge_entities(args.survivor_id, args.loser_id)
    print(f"Merged {args.loser_id} into {args.survivor_id}")


def cmd_purge(args):
    from .hygiene import purge_invalid
    store = _get_store(args.db)
    count = purge_invalid(store, older_than_days=args.older_than)
    print(f"Purged {count} entities/relations")


def cmd_rollback(args):
    from .hygiene import rollback
    store = _get_store(args.db)
    rollback(store)
    print("Ontology tables dropped")


def cmd_reextract(args):
    from .queue import enqueue
    store = _get_store(args.db)
    with store.connect() as db:
        rows = db.execute(
            "SELECT id FROM claim_units WHERE ontology_processed_at IS NULL LIMIT ?",
            (args.limit,),
        ).fetchall()
        for r in rows:
            # Reset processed flag so enqueue guards pass
            db.execute("UPDATE claim_units SET ontology_processed_at = NULL WHERE id = ?", (r["id"],))
        db.commit()  # Commit resets before enqueuing, so enqueue's own connection sees them
        count = 0
        for r in rows:
            if enqueue(store, r["id"]):
                count += 1
    print(f"Re-queued {count} claims")


def cmd_sweep_dead_letter(args):
    from .queue import sweep_dead_letter
    store = _get_store(args.db)
    count = sweep_dead_letter(store)
    print(f"Re-enqueued {count} dead-letter entries")


def cmd_enable(args):
    from .store import OntologyStore
    from .config import OntologyConfig
    store = _get_store(args.db)
    store.init()
    with store.connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO memorant_ontology_meta (key, value) VALUES ('enabled', '1')"
        )
        db.commit()
    from . import _PER_DB_ENABLED
    _PER_DB_ENABLED.clear()  # invalidate all cached enabled states
    print("Ontology enabled for", args.db)


def cmd_disable(args):
    from .store import OntologyStore
    from .config import OntologyConfig
    store = _get_store(args.db)
    store.init()
    with store.connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO memorant_ontology_meta (key, value) VALUES ('enabled', '0')"
        )
        db.commit()
    from . import _PER_DB_ENABLED
    _PER_DB_ENABLED.clear()  # invalidate all cached enabled states
    print("Ontology disabled for", args.db)


def cmd_worker(args):
    from .worker import run_worker
    db_path = args.db or "./memorant.db"
    run_worker(db_path, limit=args.limit, rpm=args.rpm)


def cmd_init(args):
    from .store import OntologyStore
    from .config import OntologyConfig
    store = OntologyStore(args.db, OntologyConfig())
    store.init()
    print(f"Ontology schema initialized for {args.db}")


def cmd_import_jsonl(args):
    import json
    from memorant import MemorantStore
    from . import patch_memorant_store
    from .config import OntologyConfig
    db_path = args.db or "./memorant.db"
    patch_memorant_store(OntologyConfig(enabled=True))
    core_store = MemorantStore(db_path)
    count = 0
    with open(args.path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "content" in data:
                try:
                    core_store.add_claim(
                        data["content"],
                        source_pointer=data.get("source_pointer", "import"),
                        source_type=data.get("source_type", "import"),
                        trust_tier=data.get("trust_tier"),
                    )
                    count += 1
                except Exception as exc:
                    print(f"Warning: skipped claim — {exc}", file=sys.stderr)
    print(f"Imported {count} claims into {db_path}")


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
