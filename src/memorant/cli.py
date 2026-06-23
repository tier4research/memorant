"""Memorant v1 CLI — local-first memory substrate for AI agents."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import MemorantStore, StoreConfig
from .trust import TrustPolicy


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="memorant", description="Memorant v1 CLI")
    p.add_argument("--db", default="./memorant.db", help="Database path")
    sub = p.add_subparsers(dest="cmd", required=True)

    # init
    sp = sub.add_parser("init", help="Initialize the database")
    sp.set_defaults(func=cmd_init)

    # add
    sp = sub.add_parser("add", help="Add a claim")
    sp.add_argument("content")
    sp.add_argument("--source", default="manual")
    sp.add_argument("--source-type", default="manual")
    sp.add_argument("--trust", default=None, choices=["operator", "verified", "derived", "untrusted"])
    sp.set_defaults(func=cmd_add)

    # search
    sp = sub.add_parser("search", help="Search claims")
    sp.add_argument("query")
    sp.add_argument("--limit", type=int, default=5)
    sp.add_argument("--as-of", default=None)
    sp.add_argument("--min-trust", default=None, choices=["operator", "verified", "derived", "untrusted"])
    sp.set_defaults(func=cmd_search)

    # search-debug
    sp = sub.add_parser("search-debug", help="Search claims with score diagnostics")
    sp.add_argument("query")
    sp.add_argument("--limit", type=int, default=5)
    sp.add_argument("--as-of", default=None)
    sp.add_argument("--min-trust", default=None, choices=["operator", "verified", "derived", "untrusted"])
    sp.add_argument("--json", action="store_true", help="Output JSON")
    sp.set_defaults(func=cmd_search_debug)

    # hygiene
    sp = sub.add_parser("hygiene", help="Report memory hygiene review candidates")
    sp.add_argument("--stale-days", type=int, default=180)
    sp.add_argument("--min-untrusted-retrievals", type=int, default=3)
    sp.add_argument("--json", action="store_true", help="Output JSON")
    sp.set_defaults(func=cmd_hygiene)

    # eval subcommands
    ev = sub.add_parser("eval", help="Evaluation helpers")
    evsub = ev.add_subparsers(dest="eval_cmd", required=True)

    sp = evsub.add_parser("retrieval", help="Evaluate retrieval from a JSONL dataset")
    sp.add_argument("--dataset", required=True, help="JSONL rows with query and expected_ids")
    sp.add_argument("--limit", type=int, default=5)
    sp.add_argument("--min-trust", default=None, choices=["operator", "verified", "derived", "untrusted"])
    sp.set_defaults(func=cmd_eval_retrieval)

    # resonate
    sp = sub.add_parser("resonate", help="Run resonance")
    sp.add_argument("context")
    sp.add_argument("--session-id", default="")
    sp.set_defaults(func=cmd_resonate)

    # supersede
    sp = sub.add_parser("supersede", help="Replace a claim with a corrected version")
    sp.add_argument("claim_id")
    sp.add_argument("new_content")
    sp.add_argument("--reason", default="superseded")
    sp.set_defaults(func=cmd_supersede)

    # correct
    sp = sub.add_parser("correct", help="Correct a claim (factual error)")
    sp.add_argument("claim_id")
    sp.add_argument("corrected_content")
    sp.add_argument("--reason", default="correction")
    sp.set_defaults(func=cmd_correct)

    # invalidate
    sp = sub.add_parser("invalidate", help="Invalidate a claim")
    sp.add_argument("claim_id")
    sp.add_argument("--reason", default="retraction")
    sp.set_defaults(func=cmd_invalidate)

    # stats
    sp = sub.add_parser("stats", help="Show store statistics")
    sp.set_defaults(func=cmd_stats)

    # doctor
    sp = sub.add_parser("doctor", help="Run health checks")
    sp.add_argument("--json", action="store_true", help="Output JSON report")
    sp.set_defaults(func=cmd_doctor)

    # backup
    sp = sub.add_parser("backup", help="Create a timestamped backup")
    sp.set_defaults(func=cmd_backup)

    # integrity-check
    sp = sub.add_parser("integrity-check", help="Run PRAGMA integrity_check")
    sp.set_defaults(func=cmd_integrity_check)

    # export
    sp = sub.add_parser("export", help="Export valid claims as JSONL")
    sp.add_argument("path", help="Output file path")
    sp.set_defaults(func=cmd_export)

    # import
    sp = sub.add_parser("import", help="Import claims from JSONL")
    sp.add_argument("path", help="Input file path")
    sp.add_argument("--source", default="import")
    sp.set_defaults(func=cmd_import)

    # migrate
    sp = sub.add_parser("migrate", help="Run pending steward migrations")
    sp.set_defaults(func=cmd_migrate)

    # Digest subcommands
    dg = sub.add_parser("digest", help="Digest management")
    dsub = dg.add_subparsers(dest="digest_cmd", required=True)

    sp = dsub.add_parser("create", help="Create a pending digest")
    sp.add_argument("--version", default=None)
    sp.set_defaults(func=cmd_digest_create)

    sp = dsub.add_parser("list", help="List digests")
    sp.add_argument("--all", action="store_true", help="Show all, not just pending")
    sp.set_defaults(func=cmd_digest_list)

    sp = dsub.add_parser("show", help="Show digest content")
    sp.add_argument("ident")
    sp.set_defaults(func=cmd_digest_show)

    sp = dsub.add_parser("diff", help="Show digest diff")
    sp.add_argument("ident")
    sp.set_defaults(func=cmd_digest_diff)

    sp = dsub.add_parser("promote", help="Promote digest to standing state")
    sp.add_argument("ident")
    sp.add_argument("--state", default="./standing_state.md")
    sp.set_defaults(func=cmd_digest_promote)

    sp = dsub.add_parser("reject", help="Reject a digest")
    sp.add_argument("ident")
    sp.add_argument("--reason", default="rejected by review")
    sp.set_defaults(func=cmd_digest_reject)

    return p


def _store(args) -> MemorantStore:
    policy = TrustPolicy(rules=[
        {"source_type": "manual", "tier": "verified"},
        {"source_type": "correction", "tier": "operator"},
    ])
    return MemorantStore(Path(args.db), StoreConfig(trust_policy=policy))


# ── Command handlers ──────────────────────────────────────────────

def cmd_init(args):
    print("initialized", ",".join(_store(args).init()))

def cmd_add(args):
    sid = _store(args).add_claim(args.content, source_pointer=args.source,
                                   source_type=args.source_type,
                                   trust_tier=args.trust)
    print(sid)

def cmd_search(args):
    for c in _store(args).search(args.query, limit=args.limit,
                                  as_of=args.as_of, min_trust=args.min_trust):
        print(f"{c.score:.3f}\t{c.trust_tier}\t{c.id}\t{c.content}")

def cmd_search_debug(args):
    results = _store(args).search_debug(
        args.query,
        limit=args.limit,
        as_of=args.as_of,
        min_trust=args.min_trust,
    )
    if args.json:
        print(json.dumps([r.__dict__ for r in results], indent=2))
        return
    for r in results:
        print(
            f"{r.score:.3f}\t{r.trust_tier}\t{r.id}\t"
            f"rank={r.rank:.6f}\trel={r.relevance:.3f}\t"
            f"reinf={r.reinforcement_bonus:.3f}\trecency={r.recency_bonus:.3f}\t"
            f"{r.content}"
        )

def cmd_hygiene(args):
    report = _store(args).hygiene_report(
        stale_days=args.stale_days,
        min_untrusted_retrievals=args.min_untrusted_retrievals,
    )
    data = report.to_dict()
    if args.json:
        print(json.dumps(data, indent=2))
        return
    for key, value in data.items():
        print(f"{key}: {len(value)}")

def cmd_eval_retrieval(args):
    store = _store(args)
    total = 0
    hits = 0
    reciprocal_ranks = []
    with open(args.dataset, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            expected = {str(x) for x in row.get("expected_ids", [])}
            if not expected:
                continue
            total += 1
            results = store.search(
                row["query"],
                limit=args.limit,
                min_trust=args.min_trust,
            )
            ids = [r.id for r in results]
            match_positions = [i + 1 for i, rid in enumerate(ids) if rid in expected]
            if match_positions:
                hits += 1
                reciprocal_ranks.append(1 / min(match_positions))
            else:
                reciprocal_ranks.append(0)

    metrics = {
        "queries": total,
        "hit_rate_at_k": hits / total if total else 0.0,
        "mrr_at_k": sum(reciprocal_ranks) / total if total else 0.0,
        "limit": args.limit,
    }
    print(json.dumps(metrics, indent=2))

def cmd_resonate(args):
    result = _store(args).resonate(args.context, session_id=args.session_id)
    print(result or "(no resonance)")

def cmd_supersede(args):
    new_id = _store(args).supersede_claim(args.claim_id, args.new_content,
                                            reason=args.reason)
    print(new_id)

def cmd_correct(args):
    new_id = _store(args).correct_claim(args.claim_id, args.corrected_content,
                                          reason=args.reason)
    print(new_id)

def cmd_invalidate(args):
    n = _store(args).invalidate_claim(args.claim_id, reason=args.reason)
    print(f"invalidated {n} claim(s)")

def cmd_stats(args):
    print(json.dumps(_store(args).stats(), indent=2))

def cmd_doctor(args):
    sys.exit(_store(args).doctor(json_output=args.json))

def cmd_backup(args):
    path = _store(args).backup()
    print(path)

def cmd_integrity_check(args):
    ok = _store(args).integrity_check()
    print("ok" if ok else "CORRUPT")
    sys.exit(0 if ok else 1)

def cmd_export(args):
    path = _store(args).export_jsonl(args.path)
    print(f"exported to {path}")

def cmd_import(args):
    n = _store(args).import_jsonl(args.path, source_pointer=args.source)
    print(f"imported {n} claim(s)")

def cmd_migrate(args):
    v = _store(args).migrate()
    print(f"migrated to version {v}")

# Digest handlers

def cmd_digest_create(args):
    print(_store(args).create_digest(version=args.version))

def cmd_digest_list(args):
    rows = _store(args).list_digests(pending_only=not args.all)
    if not rows:
        print("No pending digests." if not args.all else "No digests found.")
        return
    for r in rows:
        print(f"{r['id']}\t{r['state']}\t{r['created_at']}\t{r['version']}")

def cmd_digest_show(args):
    print(_store(args).get_digest(args.ident)["content"])

def cmd_digest_diff(args):
    print(_store(args).get_digest(args.ident)["diff_from_prior"] or "(no diff)")

def cmd_digest_promote(args):
    path = _store(args).promote_digest(args.ident, args.state)
    print(f"promoted to {path}")

def cmd_digest_reject(args):
    _store(args).reject_digest(args.ident, args.reason)
    print("rejected")


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
