# Publishing checklist

- [ ] Confirm no private memory database or WAL/SHM files are present.
- [ ] Confirm no private facts appear outside explanatory docs.
- [ ] Confirm upstream lineage in NOTICE.md.
- [ ] Run `python -m pytest`.
- [ ] Run `python -m build`.
- [ ] Push tag `v0.1.0-alpha.1`.
