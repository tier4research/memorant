# Release Notes

## Unreleased

- **Retired `scripts/patch_mcp_for_memorant.py`.** The regex-based MCP patch is
  superseded by MemPalace's first-class memorant storage backend: run the MCP
  server with `--backend memorant` (or `MEMPALACE_BACKEND=memorant`), and
  migrate existing palaces with `mempalace repair --mode migrate-to-memorant`.
  The original script is preserved at `scripts/archive/patch_mcp_for_memorant.py`
  for reference; the in-place file now exits with a pointer to the replacement.

## v1.0.0-rc.1

- Initial release candidate. Trust tiers, field-aware redaction, atomic dedup,
  FTS5 scoring, relation tracking, digest governance, doctor contract, SQLite
  steward, optional SQLCipher encryption. See `README.md` for details.
