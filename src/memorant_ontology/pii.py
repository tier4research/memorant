"""PII redaction for ontology extraction — email, phone, SSN, credit card."""

from __future__ import annotations

import re

# Email pattern
_EMAIL = re.compile(
    r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
)

# US phone: (xxx) xxx-xxxx, xxx-xxx-xxxx, xxx.xxx.xxxx, xxxxxxxxxx
_US_PHONE = re.compile(
    r'(?:\+1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}'
)

# International phone: +<country code> then groups of 2-4 digits separated by spaces/dots/dashes
_INTL_PHONE = re.compile(
    r'\+\d{1,3}(?:[\s.\-]?\d{2,4}){2,5}'
)

# SSN: xxx-xx-xxxx
_SSN = re.compile(
    r'\b\d{3}-\d{2}-\d{4}\b'
)

# Credit card: 13-19 digits, possibly with spaces/dashes
_CC = re.compile(
    r'\b(?:\d{4}[\s\-]?){3,4}\d{1,4}\b'
)


def redact(text: str) -> str:
    """Redact PII from text before sending to LLM.

    Replaces:
    - Emails → alice@[REDACTED:EMAIL]
    - US phones → [REDACTED:PHONE]
    - International phones → [REDACTED:PHONE]
    - SSNs → [REDACTED:SSN]
    - Credit cards → [REDACTED:CC]

    Returns text with PII replaced.
    """
    result = text

    # Email: keep local part, redact domain
    def _email_repl(m: re.Match) -> str:
        full = m.group(0)
        local = full.split('@')[0]
        return f"{local}@[REDACTED:EMAIL]"

    result = _EMAIL.sub(_email_repl, result)

    # SSN (before phone — phone could match SSN digits)
    result = _SSN.sub('[REDACTED:SSN]', result)

    # Credit card (before phone)
    result = _CC.sub('[REDACTED:CC]', result)

    # International phone (before US phone — +1 prefix could match both)
    result = _INTL_PHONE.sub('[REDACTED:PHONE]', result)

    # US phone
    result = _US_PHONE.sub('[REDACTED:PHONE]', result)

    return result
