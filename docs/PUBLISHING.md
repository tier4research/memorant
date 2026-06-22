# Publishing checklist

- [x] Confirm no private memory database or WAL/SHM files are present.
- [x] Confirm no private facts appear outside explanatory docs.
- [x] Confirm upstream lineage in NOTICE.md.
- [x] Run `python -m pytest`.
- [x] Run `python -m build`.
- [ ] Create the public `tier4research/memorant` repository.
- [ ] Push the `master` branch and tag `v0.1.0-alpha.1`.
- [ ] Create the GitHub release using `RELEASE_NOTES.md`.
- [ ] Enable repository security features and branch protection when available.

PyPI publication is intentionally deferred until the GitHub alpha has been
reviewed publicly. Before a PyPI upload, confirm the `memorant` distribution name
is available and publish through a trusted publisher rather than a long-lived API token.
