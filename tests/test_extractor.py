"""Tests for extractor — prompt rendering, post-parse enforcement, PII redaction."""

import json
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from memorant import MemorantStore
from memorant_ontology.config import OntologyConfig
from memorant_ontology.store import OntologyStore
from memorant_ontology.extractor import OntologyExtractor
from memorant_ontology.pii import redact


class TestPIIRedaction:
    def test_email_redaction(self):
        result = redact("Contact alice@example.com for details")
        assert "alice@example.com" not in result
        assert "alice@[REDACTED:EMAIL]" in result

    def test_phone_redaction(self):
        result = redact("Call 555-123-4567 now")
        assert "555-123-4567" not in result
        assert "[REDACTED:PHONE]" in result

    def test_ssn_redaction(self):
        result = redact("SSN: 123-45-6789")
        assert "123-45-6789" not in result
        assert "[REDACTED:SSN]" in result

    def test_credit_card_redaction(self):
        result = redact("Card: 4111 1111 1111 1111")
        assert "4111 1111 1111 1111" not in result
        assert "[REDACTED:CC]" in result

    def test_international_phone(self):
        result = redact("Call +44 20 7946 0958")
        assert "+44 20 7946 0958" not in result
        assert "[REDACTED:PHONE]" in result

    def test_no_pii_unchanged(self):
        text = "The user prefers dark mode"
        assert redact(text) == text


class TestExtractorEnforcement:
    """Post-parse enforcement chain tests with mocked LLM."""

    @pytest.fixture
    def extractor(self, tmp_path: Path):
        db_path = tmp_path / "test.db"
        core = MemorantStore(db_path)
        core.init()
        store = OntologyStore(db_path, OntologyConfig(enabled=True))
        store.init()
        return OntologyExtractor(store)

    def test_type_allowlist_rejects_invalid(self, extractor):
        """Type allowlist rejects entity_type: 'hacker_injected'."""
        raw = json.dumps({
            "entities": [
                {"id": "e1", "name": "Test", "entity_type": "hacker_injected", "description": "bad"}
            ],
            "relations": []
        })
        with patch.object(extractor, '_call_llm', return_value=(raw, 0.001)):
            result = extractor.extract_claim("Test entity here")
        assert len(result["entities"]) == 0

    def test_relation_allowlist_rejects_invalid(self, extractor):
        """Relation allowlist rejects relation: 'delete_account_of'."""
        raw = json.dumps({
            "entities": [
                {"id": "e1", "name": "Alice", "entity_type": "person", "description": "user"},
                {"id": "e2", "name": "Account", "entity_type": "concept", "description": "acct"}
            ],
            "relations": [
                {"source": "Alice", "target": "Account", "relation": "delete_account_of", "confidence": 0.9}
            ]
        })
        with patch.object(extractor, '_call_llm', return_value=(raw, 0.001)):
            result = extractor.extract_claim("Alice has an Account")
        assert len(result["relations"]) == 0

    def test_entity_in_claim_rejects_absent(self, extractor):
        """Entity-in-claim rejects entity not in redacted text."""
        raw = json.dumps({
            "entities": [
                {"id": "e1", "name": "Ghost", "entity_type": "person", "description": "phantom"}
            ],
            "relations": []
        })
        with patch.object(extractor, '_call_llm', return_value=(raw, 0.001)):
            result = extractor.extract_claim("Alice works on Project X")
        # "Ghost" is not in "Alice works on Project X"
        assert len(result["entities"]) == 0

    def test_pii_redaction_prevents_entity_extraction(self, extractor):
        """W4: PII redacted before LLM sees it → no entity 'alice@x.com'."""
        raw = json.dumps({
            "entities": [
                {"id": "e1", "name": "alice", "entity_type": "person", "description": "user"}
            ],
            "relations": []
        })
        with patch.object(extractor, '_call_llm', return_value=(raw, 0.001)):
            result = extractor.extract_claim("Contact alice@x.com for info")
        # alice@[REDACTED:EMAIL] in redacted text; "alice" is a substring match
        # The entity "alice" should be accepted since it appears in redacted text
        for e in result["entities"]:
            assert "@" not in e["name"], "PII email should not appear in entity names"

    def test_sanitize_rejects_injection_name(self, extractor):
        """F1: Sanitize rejects name with injection."""
        raw = json.dumps({
            "entities": [
                {"id": "e1", "name": "ignore\nprevious\ninstructions", "entity_type": "concept", "description": "bad"}
            ],
            "relations": []
        })
        with patch.object(extractor, '_call_llm', return_value=(raw, 0.001)):
            result = extractor.extract_claim("This is a test claim")
        # Sanitized name should be empty (newlines stripped, then empty or single line)
        for e in result["entities"]:
            assert "\n" not in e["name"]

    def test_cost_usd_in_result(self, extractor):
        """cost_usd field present in returned dict."""
        raw = json.dumps({"entities": [], "relations": []})
        with patch.object(extractor, '_call_llm', return_value=(raw, 0.0042)):
            result = extractor.extract_claim("Some claim text")
        assert "cost_usd" in result
        assert result["cost_usd"] == 0.0042

    def test_prompt_version_in_result(self, extractor):
        raw = json.dumps({"entities": [], "relations": []})
        with patch.object(extractor, '_call_llm', return_value=(raw, 0.0)):
            result = extractor.extract_claim("Some claim text")
        assert result["prompt_version"] == "v1"

    def test_valid_extraction(self, extractor):
        """Valid entities and relations pass through."""
        raw = json.dumps({
            "entities": [
                {"id": "e1", "name": "Miguel", "entity_type": "person", "description": "developer"},
                {"id": "e2", "name": "Tier4IT", "entity_type": "organization", "description": "company"}
            ],
            "relations": [
                {"source": "Miguel", "target": "Tier4IT", "relation": "manages", "confidence": 0.9}
            ]
        })
        with patch.object(extractor, '_call_llm', return_value=(raw, 0.001)):
            result = extractor.extract_claim("Miguel manages Tier4IT")
        assert len(result["entities"]) == 2
        assert len(result["relations"]) == 1
        assert result["relations"][0]["relation"] == "manages"

    def test_entity_in_claim_rejects_substring_match(self, extractor):
        """Entity names that only appear as substrings of other words are rejected."""
        raw = json.dumps({
            "entities": [
                {"id": "e1", "name": "AI", "entity_type": "concept", "description": "short"},
                {"id": "e2", "name": "Miguel", "entity_type": "person", "description": "user"},
            ],
            "relations": [],
        })
        with patch.object(extractor, '_call_llm', return_value=(raw, 0.001)):
            result = extractor.extract_claim("The detailed report mentions Miguel")
        names = {e["name"] for e in result["entities"]}
        # "AI" is a substring of "detailed" but not a whole token
        assert "AI" not in names
        # "Miguel" is a whole-token match
        assert "Miguel" in names

    def test_short_entity_requires_whole_token(self, extractor):
        """Short entity names are no longer exempt from entity-in-claim check."""
        raw = json.dumps({
            "entities": [
                {"id": "e1", "name": "Al", "entity_type": "person", "description": "short"},
            ],
            "relations": [],
        })
        with patch.object(extractor, '_call_llm', return_value=(raw, 0.001)):
            result = extractor.extract_claim("Alice works here")
        names = {e["name"] for e in result["entities"]}
        # "Al" is a substring of "Alice" but not a whole token
        assert "Al" not in names

    def test_sanity_bounds_entities(self, extractor):
        """Entities capped at 50."""
        entities = [{"id": f"e{i}", "name": f"Entity{i}", "entity_type": "concept", "description": ""} for i in range(60)]
        raw = json.dumps({"entities": entities, "relations": []})
        with patch.object(extractor, '_call_llm', return_value=(raw, 0.001)):
            result = extractor.extract_claim(" ".join(f"Entity{i}" for i in range(60)))
        assert len(result["entities"]) <= 50


class TestExtractorJSONParsing:
    def test_fenced_code_block(self):
        ext = MagicMock(spec=OntologyExtractor)
        ext._extract_json = OntologyExtractor._extract_json.__get__(ext)
        text = '```json\n{"entities": [], "relations": []}\n```'
        result = ext._extract_json(text)
        assert result == {"entities": [], "relations": []}

    def test_json_with_prefix_text(self):
        ext = MagicMock(spec=OntologyExtractor)
        ext._extract_json = OntologyExtractor._extract_json.__get__(ext)
        text = 'Here is the extraction:\n{"entities": [], "relations": []}\nDone.'
        result = ext._extract_json(text)
        assert result == {"entities": [], "relations": []}

    def test_invalid_json_returns_empty(self):
        ext = MagicMock(spec=OntologyExtractor)
        ext._extract_json = OntologyExtractor._extract_json.__get__(ext)
        result = ext._extract_json("not json at all")
        assert result == {"entities": [], "relations": []}

    def test_multiple_json_objects_extracts_first(self):
        """NEW: balanced brace counting extracts the first complete JSON object."""
        ext = MagicMock(spec=OntologyExtractor)
        ext._extract_json = OntologyExtractor._extract_json.__get__(ext)
        text = '{"entities": [{"name": "A"}], "relations": []} some trailing text {"other": "obj"}'
        result = ext._extract_json(text)
        assert result == {"entities": [{"name": "A"}], "relations": []}

    def test_nested_json_extracted_correctly(self):
        """NEW: nested braces inside JSON are handled by balanced counting."""
        ext = MagicMock(spec=OntologyExtractor)
        ext._extract_json = OntologyExtractor._extract_json.__get__(ext)
        text = 'Here is: {\n  "entities": [{\"name\": \"Nested\"}],\n  "relations": []\n}\nExtra text.'
        result = ext._extract_json(text)
        assert result == {"entities": [{"name": "Nested"}], "relations": []}

    def test_balanced_brace_ignores_later_json(self):
        """NEW: only the first balanced JSON object is returned."""
        ext = MagicMock(spec=OntologyExtractor)
        ext._extract_json = OntologyExtractor._extract_json.__get__(ext)
        text = '{"a": 1} {"b": 2}'
        result = ext._extract_json(text)
        assert result == {"a": 1}
