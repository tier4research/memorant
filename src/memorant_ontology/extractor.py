"""LLM-based ontology extractor with post-parse enforcement chain.

Post-parse enforcement order (per W4):
1. Schema validate (JSON structure)
2. Type allowlist
3. Relation allowlist
4. Entity-in-claim check (against post-redaction text)
5. Sanity bounds (entities <= 50, relations <= 100)
6. Sanitize every name + description
7. Return dict with entities, relations, prompt_version, raw_response, cost_usd
"""

from __future__ import annotations

import json
import re
import uuid
from typing import TYPE_CHECKING

from .normalizer import sanitize_name, sanitize_description, normalize_name
from .pii import redact

if TYPE_CHECKING:
    from .config import OntologyConfig
    from .store import OntologyStore


class LLMError(Exception):
    """Raised when the LLM call fails or the provider is unavailable."""
    pass


class OntologyExtractor:
    """Extracts entities and relations from claims via LLM.

    Provides enqueue, extraction, and dry_run methods.
    """

    def __init__(self, store: "OntologyStore"):
        self.store = store
        self.config = store.config

    def enqueue(self, claim_id: str) -> bool:
        """Enqueue a claim for ontology extraction. Returns True if new row."""
        from .queue import enqueue
        return enqueue(self.store, claim_id)

    def extract_claim(self, content: str, config: "OntologyConfig | None" = None) -> dict:
        """Extract entities and relations from claim content.

        Returns:
            {
                "entities": [...],
                "relations": [...],
                "prompt_version": str,
                "raw_response": str,
                "cost_usd": float,
            }
        """
        cfg = config or self.config

        # PII redaction before LLM call
        redacted = redact(content) if cfg.redact_pii else content

        # Render prompt
        from .prompt import render_prompt
        prompt = render_prompt(redacted, cfg)

        # LLM call (provider boundary — can be mocked)
        raw_response, cost_usd = self._call_llm(prompt, cfg)

        # Parse and enforce
        result = self._parse_and_enforce(raw_response, redacted, cfg)
        result["prompt_version"] = cfg.prompt_version
        result["raw_response"] = raw_response
        result["cost_usd"] = cost_usd

        return result

    def dry_run(self, claim_id: str, store: "OntologyStore | None" = None) -> dict:
        """Return what would be extracted without writing to DB."""
        s = store or self.store
        with s.connect() as db:
            row = db.execute(
                "SELECT content FROM claim_units WHERE id = ?", (claim_id,)
            ).fetchone()
        if not row:
            raise ValueError(f"Claim not found: {claim_id}")
        return self.extract_claim(row["content"])

    def _call_llm(self, prompt: str, config: "OntologyConfig") -> tuple[str, float]:
        """Call the LLM provider. Returns (response_text, cost_usd).

        Override in tests to mock.
        """
        try:
            import litellm
            response = litellm.completion(
                model=f"{config.provider}/{config.model}",
                messages=[{"role": "user", "content": prompt}],
                timeout=config.timeout_seconds,
            )
            text = response.choices[0].message.content or ""
            cost = 0.0
            if hasattr(response, "usage") and response.usage:
                # Estimate cost from token counts
                prompt_tokens = getattr(response.usage, "prompt_tokens", 0)
                completion_tokens = getattr(response.usage, "completion_tokens", 0)
                cost = (prompt_tokens * 0.000001 + completion_tokens * 0.000002)
            return text, cost
        except ImportError as exc:
            # litellm not installed — extraction cannot proceed
            raise LLMError("litellm is not installed; ontology extraction unavailable") from exc
        except Exception as exc:
            raise LLMError(f"LLM call failed: {exc}") from exc

    def _parse_and_enforce(
        self, raw: str, redacted_text: str, config: "OntologyConfig"
    ) -> dict:
        """Parse LLM response and apply enforcement chain."""
        # Step 1: Schema validate — parse JSON
        parsed = self._extract_json(raw)
        entities = parsed.get("entities", [])
        relations = parsed.get("relations", [])

        # Ensure lists
        if not isinstance(entities, list):
            entities = []
        if not isinstance(relations, list):
            relations = []

        # Step 5: Sanity bounds (do early to cap work)
        entities = entities[:50]
        relations = relations[:100]

        # Step 6: Sanitize every name + description (F1)
        clean_entities = []
        for e in entities:
            name = sanitize_name(str(e.get("name", "")))
            if not name:
                continue
            desc = sanitize_description(str(e.get("description", "")))
            entity_type = str(e.get("entity_type", "")).strip().lower()

            # Step 2: Type allowlist
            if entity_type not in config.entity_types_allowed:
                continue

            clean_entities.append({
                "id": str(uuid.uuid4()),
                "name": name,
                "entity_type": entity_type,
                "description": desc,
            })

        # Step 4: Entity-in-claim check (multi-word aware via whole-word regex)
        redacted_lower = normalize_name(redacted_text)

        verified_entities = []
        for e in clean_entities:
            nn = normalize_name(e["name"])
            # Multi-word safe: match on whole-word boundaries in normalized text
            escaped = re.escape(nn)
            if re.search(r'\b' + escaped + r'\b', redacted_lower):
                verified_entities.append(e)
        # Use verified entities set for relation checks
        verified_names = {normalize_name(e["name"]) for e in verified_entities}

        # Step 3: Relation allowlist + entity-in-claim
        clean_relations = []
        for r in relations:
            relation = str(r.get("relation", "")).strip().lower()

            # Relation allowlist
            if relation not in config.relations_allowed:
                continue

            source = sanitize_name(str(r.get("source", "")))
            target = sanitize_name(str(r.get("target", "")))
            if not source or not target:
                continue

            # Entity-in-claim: both source and target must be in verified entities
            if normalize_name(source) not in verified_names:
                continue
            if normalize_name(target) not in verified_names:
                continue

            confidence = float(r.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))

            clean_relations.append({
                "source": source,
                "target": target,
                "relation": relation,
                "confidence": confidence,
            })

        return {
            "entities": verified_entities,
            "relations": clean_relations,
        }

    def _tokenize(self, text: str) -> set[str]:
        """Tokenize text into a set of lowercase alphanumeric words."""
        return set(re.findall(r"\w+", text.lower()))

    def _extract_json(self, text: str) -> dict:
        """Extract JSON from LLM response, handling fenced blocks and noise."""
        # Try direct parse first
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting from fenced code block
        m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try finding balanced JSON object(s) with brace counting (L1 string-aware)
        start = text.find('{')
        if start != -1:
            depth = 0
            in_string = False
            end = start
            i = start
            while i < len(text):
                ch = text[i]
                if ch == '\\':
                    i += 2  # skip escaped char
                    continue
                if ch == '"':
                    in_string = not in_string
                elif not in_string:
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            end = i
                            break
                i += 1
            if end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    pass

        # Last resort: empty
        return {"entities": [], "relations": []}
