"""Prompt template for ontology extraction from claim units.

Allowlists from config are injected into the prompt.
Render-time sanitization prevents injection via entity names.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import OntologyConfig


# Prompt-injection tokens that should be neutralized before a claim is placed
# into the extraction prompt. This is defense-in-depth; the claim has already
# been PII-redacted and name-sanitized upstream.
_PROMPT_OVERRIDE_PATTERNS = [
    r"(?i)\bignore (?:all )?(?:previous|above|the above) (?:instructions?|rules?|prompts?)\b",
    r"(?i)\bforget (?:all )?(?:previous|above|the above) (?:instructions?|rules?|prompts?)\b",
    r"(?i)\bdon[']?t (?:follow|obey) (?:instructions?|rules?|prompts?)\b",
    r"(?i)\bthis is (?:a|an) (?:system|instruction)[^\w]",
    r"(?i)\[/system\b",
    r"(?i)\[/instructions\b",
]


def _sanitize_prompt_content(content: str) -> str:
    """Neutralize common prompt-injection markers in claim content.

    Removes structural delimiters that could break the prompt layout and
    strips/replaces instruction-override phrases.
    """
    sanitized = content
    for pattern in _PROMPT_OVERRIDE_PATTERNS:
        sanitized = re.sub(pattern, "[REDACTED]", sanitized)
    # Remove structural tokens that could be mistaken for prompt boundaries
    sanitized = re.sub(r"\[/?(DATA|ANDRE_[A-Z]+)\]", "", sanitized)
    return sanitized


def render_prompt(content: str, config: "OntologyConfig") -> str:
    """Render the extraction prompt for a claim.

    Injects allowlists from config (entity_types, relations).
    Content is sanitized at render time to reduce prompt-injection surface.
    PII redaction happens in extractor before this.
    """
    entity_types = ", ".join(config.entity_types_allowed)
    relations = ", ".join(config.relations_allowed)
    safe_content = _sanitize_prompt_content(content)

    return f"""You are an ontology extractor. Given a claim, extract entities and relations.

RULES:
- Entity types MUST be one of: {entity_types}
- Relations MUST be one of: {relations}
- Every entity MUST appear in the claim text (after redaction)
- Extract at most 50 entities and 100 relations
- For each entity: id (uuid), name, entity_type, description (<=120 chars)
- For each relation: source (entity name), target (entity name), relation, confidence (0.0-1.0)
- Return ONLY valid JSON

CLAIM:
[DATA]
{safe_content}
[/DATA]

OUTPUT FORMAT:
{{"entities": [{{"id": "...", "name": "...", "entity_type": "...", "description": "..."}}], "relations": [{{"source": "...", "target": "...", "relation": "...", "confidence": 0.5}}]}}

JSON:"""
