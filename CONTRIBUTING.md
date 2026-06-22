# Contributing to Memorant

Thanks for helping improve Memorant. Bug reports, documentation fixes, tests, and
focused feature proposals are welcome.

## Development setup

```bash
git clone https://github.com/tier4research/memorant.git
cd memorant
python -m venv .venv
python -m pip install -e ".[test]"
python -m pytest
```

## Pull requests

Keep changes focused, add or update tests for behavioral changes, and update the
README or release notes when public behavior changes. All tests must pass on every
supported Python version. Do not commit databases, memory contents, credentials,
personal data, generated build artifacts, or model outputs containing private data.

By contributing, you agree that your contribution is licensed under the Apache
License 2.0.
