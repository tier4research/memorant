from __future__ import annotations
import argparse
from pathlib import Path
from .core import MemoryPalace

def palace(args): return MemoryPalace(Path(args.db))
def cmd_init(args): print("initialized", ",".join(palace(args).init()))
def cmd_add(args): print(palace(args).add_claim(args.content, source_pointer=args.source, source_type=args.source_type))
def cmd_search(args):
    for c in palace(args).search(args.query, limit=args.limit, as_of=args.as_of): print(f"{c.score:.3f}\t{c.id}\t{c.content}")
def cmd_resonate(args): print(palace(args).resonate(args.context, session_id=args.session_id) or "(no resonance)")
def cmd_digest_create(args): print(palace(args).create_digest(version=args.version))
def cmd_digest_list(args):
    rows = palace(args).list_digests(pending_only=not args.all)
    if not rows: print("No pending digests." if not args.all else "No digests found."); return
    for r in rows: print(f"{r['id']}\t{r['promoted']}\t{r['created_at']}\t{r['version']}")
def cmd_digest_show(args): print(palace(args).get_digest(args.ident)["content"])
def cmd_digest_diff(args): print(palace(args).get_digest(args.ident)["diff_from_prior"] or "(no diff)")
def cmd_digest_promote(args):
    if not args.yes: raise SystemExit("Refusing to promote without --yes in alpha CLI")
    print(palace(args).promote_digest(args.ident, args.state))
def cmd_digest_reject(args):
    if not args.yes: raise SystemExit("Refusing to reject without --yes in alpha CLI")
    palace(args).reject_digest(args.ident, args.reason); print("rejected")
def build_parser():
    p = argparse.ArgumentParser(prog="memorant", description="Memorant CLI"); p.add_argument("--db", default="./palace.db"); sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("init"); sp.set_defaults(func=cmd_init)
    sp = sub.add_parser("add"); sp.add_argument("content"); sp.add_argument("--source", default="manual"); sp.add_argument("--source-type", default="manual"); sp.set_defaults(func=cmd_add)
    sp = sub.add_parser("search"); sp.add_argument("query"); sp.add_argument("--limit", type=int, default=5); sp.add_argument("--as-of", default=None); sp.set_defaults(func=cmd_search)
    sp = sub.add_parser("resonate"); sp.add_argument("context"); sp.add_argument("--session-id", default=""); sp.set_defaults(func=cmd_resonate)
    dg = sub.add_parser("digest"); dsub = dg.add_subparsers(dest="digest_cmd", required=True)
    sp = dsub.add_parser("create"); sp.add_argument("--version", default=None); sp.set_defaults(func=cmd_digest_create)
    sp = dsub.add_parser("list"); sp.add_argument("--all", action="store_true"); sp.set_defaults(func=cmd_digest_list)
    sp = dsub.add_parser("show"); sp.add_argument("ident"); sp.set_defaults(func=cmd_digest_show)
    sp = dsub.add_parser("diff"); sp.add_argument("ident"); sp.set_defaults(func=cmd_digest_diff)
    sp = dsub.add_parser("promote"); sp.add_argument("ident"); sp.add_argument("--state", default="./standing_state.md"); sp.add_argument("--yes", action="store_true"); sp.set_defaults(func=cmd_digest_promote)
    sp = dsub.add_parser("reject"); sp.add_argument("ident"); sp.add_argument("--reason", default="rejected by review"); sp.add_argument("--yes", action="store_true"); sp.set_defaults(func=cmd_digest_reject)
    return p
def main(argv=None): args = build_parser().parse_args(argv); args.func(args); return 0
if __name__ == "__main__": raise SystemExit(main())
