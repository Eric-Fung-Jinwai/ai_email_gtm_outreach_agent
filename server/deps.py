"""Shared FastAPI dependencies.

Two are deliberately overridable via ``app.dependency_overrides`` so tests run
offline against a temp DB with fake agents (point 9):
``get_db_path`` and ``get_agents_factory``.
"""

from typing import Callable, Optional
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request, status

from backend.agents import Agents, build_agents
from backend.config import get_settings


def get_db_path() -> Optional[str]:
    """App DB path. Overridden in tests to point at a temp file."""
    return get_settings().app_db_path


AgentsFactory = Callable[[str], Agents]


def get_agents_factory() -> AgentsFactory:
    """Factory that builds the real agno-backed agents for a given email style.
    Overridden in tests to inject fakes (no network, no keys)."""
    return build_agents


def current_user(request: Request) -> int:
    """The authenticated user's id, from the signed session cookie. 401 if absent."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    return int(user_id)


def verify_origin(request: Request) -> None:
    """CSRF defense for cookie-authenticated mutations (point 7).

    Signed session cookies are sent automatically by the browser, so a
    cross-origin page could otherwise trigger state-changing POSTs. When the
    browser sends an ``Origin`` (or ``Referer``), we require it to be allowed:
    against ``TRUSTED_ORIGINS`` when configured (correct behind a reverse proxy,
    where the request host is an internal name/port), otherwise against the request
    host. Non-browser clients that omit both headers are allowed (they aren't riding
    a victim's cookie). A shared CSRF token would be a stronger control and is the
    documented next step."""
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return
    parsed = urlparse(source)
    trusted = get_settings().trusted_origin_list()
    if trusted:
        src_origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        if src_origin not in trusted:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="cross-origin request rejected")
    elif parsed.netloc and parsed.netloc != request.url.netloc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="cross-origin request rejected")


# Convenience aliases for route signatures.
CurrentUser = Depends(current_user)
DbPath = Depends(get_db_path)
AgentsFactoryDep = Depends(get_agents_factory)
OriginGuard = Depends(verify_origin)
