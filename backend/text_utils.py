"""Shared text helpers."""

import re
from typing import Any, Optional
from urllib.parse import urlparse

_LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation",
    "co", "company", "gmbh", "plc", "sa", "ag",
}

# Pragmatic email syntax check (not RFC-complete): one @, a dot in the domain.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(email: str) -> bool:
    return bool(email and _EMAIL_RE.match(email.strip()))


def safe_http_url(url: Any) -> Optional[str]:
    """Return ``url`` only if it is a plain http(s) link, else ``None``.

    Research/job-posting URLs originate from model output and third-party APIs, so
    they are untrusted. Blocking everything but http/https keeps ``javascript:``,
    ``data:``, and other scheme-based injection out of rendered ``href``s."""
    if not isinstance(url, str):
        return None
    u = url.strip()
    scheme = urlparse(u).scheme.lower()
    return u if scheme in ("http", "https") else None


def canonical_host(url: str) -> Optional[str]:
    """Best-effort registrable hostname from a URL/website; ``None`` if unusable."""
    if not url:
        return None
    u = url.strip()
    if "://" not in u:
        u = "http://" + u
    host = (urlparse(u).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def email_domain(email: str) -> Optional[str]:
    if not email or "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].strip().lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain or None


def normalize_company_name(name: str) -> str:
    """Lowercase, strip punctuation and legal suffixes → a stable match key.

    Used to compare company identities across the app (JSearch employer matching,
    contact-cooldown suppression), so "Acme, Inc." and "acme" collapse to "acme".
    """
    s = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    tokens = [t for t in s.split() if t and t not in _LEGAL_SUFFIXES]
    return " ".join(tokens).strip()
