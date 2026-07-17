"""Transient-error retry for agent calls (Phase 7).

``RetryingAgent`` wraps an agent and retries only *transient* failures (rate
limits, timeouts, connection blips, 5xx) with exponential backoff. Deterministic
errors (400/401, JSON/validation) are NOT retried — retrying them just wastes
time and quota. Consistent with the ``MeteringAgent`` wrapper pattern.
"""

from typing import Any, Callable, Optional

from tenacity import (
    AsyncRetrying,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

# Substrings in an exception class name that indicate a transient failure, used
# when the exception carries no HTTP status code.
_TRANSIENT_NAME_HINTS = (
    "ratelimit", "timeout", "timedout", "apiconnection", "connectionerror",
    "serviceunavailable", "internalserver", "overloaded", "temporarilyunavailable",
)


def is_transient_error(exc: BaseException) -> bool:
    """True if the error is worth retrying (rate limit / timeout / 5xx / connection)."""
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = getattr(exc, "status", None)
    if isinstance(status, int):
        return status in (408, 409, 429) or status >= 500
    name = type(exc).__name__.lower()
    return any(hint in name for hint in _TRANSIENT_NAME_HINTS)


class RetryingAgent:
    """Delegates to an agent, retrying transient failures with backoff."""

    def __init__(
        self,
        inner: Any,
        *,
        max_attempts: int = 3,
        wait_multiplier: float = 0.5,
        is_transient: Optional[Callable[[BaseException], bool]] = None,
    ) -> None:
        self._inner = inner
        self._max_attempts = max(1, max_attempts)
        self._wait_multiplier = wait_multiplier
        self._is_transient = is_transient or is_transient_error

    def _policy(self) -> dict:
        return dict(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential(multiplier=self._wait_multiplier, max=8),
            retry=retry_if_exception(self._is_transient),
            reraise=True,
        )

    async def arun(self, prompt: str) -> Any:
        # NOTE: agno's ``Agent.arun`` is a *sync* method that RETURNS a coroutine
        # (it is not itself a coroutine function). tenacity's ``AsyncRetrying``
        # expects a coroutine function and would hand back the un-awaited coroutine.
        # Wrap the call in a real coroutine function and await the awaitable here, so
        # retries see the resolved result. Works for both agno and async-def fakes.
        async def _call() -> Any:
            return await self._inner.arun(prompt)

        return await AsyncRetrying(**self._policy())(_call)

    def run(self, prompt: str) -> Any:
        return Retrying(**self._policy())(self._inner.run, prompt)
