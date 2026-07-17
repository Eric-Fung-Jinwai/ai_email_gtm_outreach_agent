"""Minimal in-memory sliding-window rate limiter for auth endpoints.

Password verification runs PBKDF2 with 600k iterations — deliberately expensive,
which also makes unauthenticated ``/login`` / ``/register`` a CPU-DoS lever. This
caps attempts per client so a single source can't spin the CPU (High #5).

Scope caveat: like the SSE job registry, this is per-process. A multi-worker or
multi-instance deployment should enforce limits at the proxy/edge too; this is the
app-level floor, not the whole defense.
"""

import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import HTTPException, Request, status


class SlidingWindowLimiter:
    def __init__(self, max_attempts: int, window_seconds: float, max_keys: int = 10_000) -> None:
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._max_keys = max_keys  # bound memory: spoofed client IPs can't grow this unbounded
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._since_prune = 0

    def _prune(self, now: float) -> None:
        """Drop keys whose window has fully expired; hard-reset if still oversized
        (a flood of unique keys degrades to a fresh window rather than OOM)."""
        for k in list(self._hits):
            q = self._hits[k]
            while q and now - q[0] > self.window:
                q.popleft()
            if not q:
                del self._hits[k]
        if len(self._hits) > self._max_keys:
            self._hits.clear()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        # Amortized cleanup: sweep periodically or when the table grows too large.
        self._since_prune += 1
        if self._since_prune >= 256 or len(self._hits) > self._max_keys:
            self._since_prune = 0
            self._prune(now)
        q = self._hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.max_attempts:
            return False
        q.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()
        self._since_prune = 0


# 10 auth attempts per client IP per minute — generous for humans, throttles a
# scripted PBKDF2 grind.
_AUTH_LIMITER = SlidingWindowLimiter(max_attempts=10, window_seconds=60.0)


def auth_rate_limit(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    if not _AUTH_LIMITER.allow(f"auth:{client}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many attempts — please wait a minute and try again",
        )
