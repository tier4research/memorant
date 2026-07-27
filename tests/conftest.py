"""Shared fixtures for memorant_ontology tests."""

import sqlite3
from pathlib import Path

import pytest

from memorant import MemorantStore
from memorant_ontology.config import OntologyConfig
from memorant_ontology.store import OntologyStore


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Return a temporary database path."""
    return tmp_path / "test.db"


@pytest.fixture
def core_store(tmp_db: Path) -> MemorantStore:
    """Return an initialized MemorantStore."""
    store = MemorantStore(tmp_db)
    store.init()
    return store


@pytest.fixture
def ontology_store(tmp_db: Path, core_store: MemorantStore) -> OntologyStore:
    """Return an initialized OntologyStore (schema applied)."""
    store = OntologyStore(tmp_db, OntologyConfig(enabled=True))
    store.init()
    return store


@pytest.fixture
def ontology_config() -> OntologyConfig:
    """Return a default OntologyConfig with enabled=True."""
    return OntologyConfig(enabled=True)


@pytest.fixture
def restore_patch():
    """Restore MemorantStore.add_claim after CLI tests that patch it."""
    from memorant import MemorantStore
    from memorant_ontology import _PATCHED

    original = MemorantStore.add_claim
    _PATCHED.clear()
    _PATCHED.append(False)
    yield
    MemorantStore.add_claim = original
    _PATCHED.clear()
    _PATCHED.append(False)
