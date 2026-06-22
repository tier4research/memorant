"""Trust tiers, provenance, and secret redaction for Memorant v1.

Trust Tiers (in descending authority):
- operator: Manually assigned, highest trust. Always survives resonance filtering.
- verified: Verified by a trusted process or cross-referenced.
- derived: Computed/derived from other claims. Inherits minimum source trust.
- untrusted: Default for imported or unvalidated claims. Never auto-resonates.

Redaction:
- Field-aware (not whole-line) secret redaction
- Targets: API keys, tokens, passwords, private keys
- Benign terms (SQL, debug, tokenization) must survive redaction
- Operates on individual tokens within a line, not the whole line
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


class TrustTier:
    """Trust tier constants and ordering."""

    OPERATOR = "operator"
    VERIFIED = "verified"
    DERIVED = "derived"
    UNTRUSTED = "untrusted"

    _RANK = {OPERATOR: 0, VERIFIED: 1, DERIVED: 2, UNTRUSTED: 3}

    @classmethod
    def rank(cls, tier: str) -> int:
        return cls._RANK.get(tier, cls._RANK[cls.UNTRUSTED])

    @classmethod
    def is_at_least(cls, tier: str, minimum: str) -> bool:
        return cls.rank(tier) <= cls.rank(minimum)

    @classmethod
    def allowed_for_resonance(cls) -> list[str]:
        """Only operator and verified claims may auto-resonate."""
        return [cls.OPERATOR, cls.VERIFIED]


@dataclass
class TrustPolicy:
    """Configuration for trust tier assignment.

    Rules are checked in order; the first matching rule wins.
    Default assigns 'untrusted' to everything.
    """

    rules: list[dict] = field(default_factory=list)
    default_tier: str = TrustTier.UNTRUSTED

    def evaluate(self, source_type: str, source_pointer: str) -> str:
        """Determine trust tier for a claim based on its source."""
        for rule in self.rules:
            st_match = rule.get("source_type")
            sp_match = rule.get("source_pointer")
            if st_match and not _pattern_match(st_match, source_type):
                continue
            if sp_match and not _pattern_match(sp_match, source_pointer):
                continue
            return rule.get("tier", self.default_tier)
        return self.default_tier


def _pattern_match(pattern: str, value: str) -> bool:
    """Simple glob or exact match."""
    if pattern == "*":
        return True
    if "*" in pattern:
        regex = re.escape(pattern).replace(r"\*", ".*")
        return bool(re.match(f"^{regex}$", value))
    return pattern == value


def assign_trust(
    policy: TrustPolicy,
    source_type: str,
    source_pointer: str,
) -> str:
    """Assign a trust tier using the configured policy."""
    return policy.evaluate(source_type, source_pointer)


# ── Secret redaction ──────────────────────────────────────────────

# Patterns for secrets that must be redacted
_SECRET_PATTERNS = [
    # API keys (various formats)
    (re.compile(r'(?:api[_-]?key|apikey|api_secret|secret_key)\s*[:=]\s*["\']?([^\s"\'\)]{8,})["\']?', re.IGNORECASE),
     lambda m: f'{m.group(0)[:m.start(1)-m.start(0)]}[REDACTED:API_KEY]'),
    # Bearer tokens
    (re.compile(r'(?:bearer|token)\s+([A-Za-z0-9_\-\.]{20,})', re.IGNORECASE),
     lambda m: f'{m.group(0)[:m.start(1)-m.start(0)]}[REDACTED:TOKEN]'),
    # GitHub PATs (ghp_, github_pat_)
    (re.compile(r'(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}'),
     lambda m: '[REDACTED:GITHUB_TOKEN]'),
    # Private keys (PEM headers) — match between BEGIN and END markers
    (re.compile(r'-----BEGIN (?:RSA|EC|DSA|OPENSSH|PGP) PRIVATE KEY-----.*?-----END [A-Z ]+ PRIVATE KEY-----', re.DOTALL),
     lambda m: '[REDACTED:PRIVATE_KEY]'),
    # JWT tokens (eyJ...)
    (re.compile(r'eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}'),
     lambda m: '[REDACTED:JWT]'),
    # Generic key=value with suspicious names
    (re.compile(r'(?:password|passwd|pwd|secret|credential)\s*[:=]\s*["\']?([^\s"\'\)]{4,})["\']?', re.IGNORECASE),
     lambda m: f'{m.group(0)[:m.start(1)-m.start(0)]}[REDACTED:CREDENTIAL]'),
]

# Benign terms that must NOT be redacted even if they appear near matches
_BENIGN_TERMS = {
    "sql", "debug", "tokenization", "tokenize", "tokenizer",
    "embedding", "embed", "api", "keyboard",
}

REDACT_PATTERNS = _SECRET_PATTERNS  # Export for external use


def redact_content(content: str) -> str:
    """Apply field-aware redaction to claim content.

    Unlike the alpha's sanitize_line (which dropped entire lines containing
    leak markers), this performs targeted redaction on just the secret portions
    of the text. Benign terms like 'SQL', 'debug', and 'tokenization' survive.
    """
    result = content

    for pattern, replacement_fn in _SECRET_PATTERNS:
        # Check if the captured secret value contains benign terms —
        # if so, skip redaction for that specific match.
        # Patterns without capture groups skip the benign check.
        def _safe_replacement(m):
            # Check group(1) (the captured secret value) for benign terms
            try:
                secret = m.group(1)
            except IndexError:
                secret = None

            if secret is not None:
                lower_secret = secret.lower()
                if any(term in lower_secret for term in _BENIGN_TERMS):
                    return m.group(0)  # Don't redact — benign in the secret value
            return replacement_fn(m)

        result = pattern.sub(_safe_replacement, result)

    # Truncate long lines but preserve meaning
    if len(result) > 240:
        result = result[:237] + "..."

    return result.strip()


def is_redaction_safe(content: str, benign_terms: set[str] | None = None) -> bool:
    """Verify that benign terms survive redaction.

    Returns True if all specified benign terms appear in the redacted output.
    """
    if benign_terms is None:
        benign_terms = _BENIGN_TERMS
    redacted = redact_content(content)
    for term in benign_terms:
        if term.lower() in content.lower() and term.lower() not in redacted.lower():
            return False
    return True
