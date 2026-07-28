"""Tests for OntologyConfig validation."""

import pytest

from memorant_ontology.config import OntologyConfig


class TestOntologyConfigValidation:
    def test_default_allowlists_valid(self):
        """Default config passes validation."""
        cfg = OntologyConfig(enabled=True)
        assert "person" in cfg.entity_types_allowed
        assert "manages" in cfg.relations_allowed

    def test_invalid_entity_type_chars(self):
        """Entity type with invalid characters is rejected."""
        with pytest.raises(ValueError, match="invalid characters"):
            OntologyConfig(entity_types_allowed=["bad space!"])

    def test_invalid_relation_chars(self):
        """Relation with invalid characters is rejected."""
        with pytest.raises(ValueError, match="invalid characters"):
            OntologyConfig(relations_allowed=["has-space"])

    def test_functional_relation_subset(self):
        """Functional relations must be a subset of allowed relations."""
        with pytest.raises(ValueError, match="functional_relations"):
            OntologyConfig(
                relations_allowed=["manages", "uses"],
                functional_relations=["located_in"],
            )

    def test_allowlists_must_be_iterable(self):
        """Allowlists must be list/tuple, not a string."""
        with pytest.raises(ValueError, match="must be a list or tuple"):
            OntologyConfig(entity_types_allowed="person")

    def test_allowlist_entries_must_be_strings(self):
        """Allowlist entries must be strings."""
        with pytest.raises(ValueError, match="must be strings"):
            OntologyConfig(entity_types_allowed=["person", 123])

    def test_version_matches_pyproject(self):
        """NEW: memorant_ontology.__version__ matches pyproject.toml target."""
        from memorant_ontology import __version__
        # Should be a semver-compatible string, not the old "0.1.0"
        assert __version__ == "1.0.0-rc.3"
        # Verify it's not the stale version
        assert __version__ != "0.1.0"
