#!/usr/bin/env python3
"""RETIRED — replaced by the mempalace memorant backend.

The regex patch this script applied is no longer needed. Use instead:

    mempalace-mcp --backend memorant            # new palaces
    mempalace repair --mode migrate-to-memorant # migrate an existing palace

The original script is preserved at scripts/archive/patch_mcp_for_memorant.py
for historical reference.
"""

import sys

sys.exit(
    "patch_mcp_for_memorant.py is retired. Use `mempalace-mcp --backend memorant` "
    "(see scripts/archive/patch_mcp_for_memorant.py for the archived original)."
)
