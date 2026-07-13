"""Shared text helpers."""

import re

_LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation",
    "co", "company", "gmbh", "plc", "sa", "ag",
}


def normalize_company_name(name: str) -> str:
    """Lowercase, strip punctuation and legal suffixes → a stable match key.

    Used to compare company identities across the app (JSearch employer matching,
    contact-cooldown suppression), so "Acme, Inc." and "acme" collapse to "acme".
    """
    s = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    tokens = [t for t in s.split() if t and t not in _LEGAL_SUFFIXES]
    return " ".join(tokens).strip()
