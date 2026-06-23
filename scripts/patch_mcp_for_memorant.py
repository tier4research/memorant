#!/usr/bin/env python3
"""Patch hermes_mcp_server.py to use Memorant instead of Palace V2.

Replaces the search_palace MCP tool function with a Memorant-backed version.
Backs up the original file first.

Usage:
  /opt/hermes/.venv/bin/python3 patch_mcp_for_memorant.py
  /opt/hermes/.venv/bin/python3 patch_mcp_for_memorant.py --dry-run
"""

import re
import sys
import os
from datetime import datetime

TARGET = "/opt/data/hermes_mcp_server.py"


def patch(dry_run: bool = False) -> bool:
    if not os.path.exists(TARGET):
        print(f"ERROR: {TARGET} not found")
        return False

    with open(TARGET, encoding="utf-8") as f:
        original = f.read()

    # ── The replacement function ─────────────────────────────
    new_search_palace = '''async def search_palace(query: str, limit: int = 6) -> str:
    """Search Elle's memory palace using Memorant FTS5 with trust filtering.

    Searches claim_units table for facts matching the query. Returns JSON with
    results array containing id, content, score, and trust tier. Filters to
    derived+ trust (excludes untrusted/quarantine claims).
    """
    import json as _json
    try:
        from memorant import MemorantStore
        store = MemorantStore("/opt/data/memory_palace_v2/memorant.db")
        store.init()
        claims = store.search(query, limit=limit, min_trust="derived")
        results = []
        for c in claims:
            results.append({
                "id": c.id,
                "content": c.content,
                "score": round(c.score, 3),
                "trust": c.trust_tier,
            })
        return _json.dumps({
            "results": results,
            "query": query,
            "total": len(results),
            "backend": "memorant",
        })
    except Exception as e:
        return _json.dumps({
            "error": str(e),
            "query": query,
            "backend": "memorant",
        })
'''

    # ── Find and replace the old function ────────────────────
    # Pattern: async def search_palace(...) through to the next @mcp.tool or end
    old_pattern = r'async def search_palace\([^)]*\)\s*->\s*str\s*:.*?(?=\n(?:@mcp\.|async def |def |class |# ---|$))'

    match = re.search(old_pattern, original, re.DOTALL)
    if not match:
        print("ERROR: Could not find search_palace function in MCP server")
        print("Searching for 'search_palace' in file...")
        for i, line in enumerate(original.split("\n"), 1):
            if "search_palace" in line:
                print(f"  Line {i}: {line.strip()[:120]}")
        return False

    old_func = match.group(0)
    print(f"Found old search_palace: {len(old_func)} chars")
    print(f"  Starts: {old_func[:80].strip()}...")
    print()

    if dry_run:
        print("DRY RUN — would replace search_palace with Memorant version")
        print(f"New function: {len(new_search_palace)} chars")
        return True

    # Replace
    patched = original.replace(old_func, new_search_palace)

    if patched == original:
        print("ERROR: No changes applied — replacement didn't match")
        return False

    # Backup
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{TARGET}.bak-{ts}"
    with open(backup, "w", encoding="utf-8") as f:
        f.write(original)
    print(f"Backup: {backup}")

    # Write
    with open(TARGET, "w", encoding="utf-8") as f:
        f.write(patched)
    print(f"Patched: {TARGET}")

    # Verify syntax
    import py_compile
    try:
        py_compile.compile(TARGET, doraise=True)
        print("Syntax: OK")
    except py_compile.PyCompileError as e:
        print(f"Syntax ERROR: {e}")
        # Restore backup
        with open(backup, encoding="utf-8") as f:
            original_content = f.read()
        with open(TARGET, "w", encoding="utf-8") as f:
            f.write(original_content)
        print(f"RESTORED from backup due to syntax error")
        return False

    return True


def main():
    dry_run = "--dry-run" in sys.argv
    print(f"Patch MCP server for Memorant")
    print(f"  Target: {TARGET}")
    print(f"  Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print()

    success = patch(dry_run=dry_run)
    if success:
        print()
        print("DONE — restart gateway for changes to take effect")
    else:
        print()
        print("FAILED — see errors above")
        sys.exit(1)


if __name__ == "__main__":
    main()
