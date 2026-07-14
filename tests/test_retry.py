import asyncio

import pytest

from backend.retry import RetryingAgent, is_transient_error


class _StatusError(Exception):
    def __init__(self, status_code):
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class RateLimitError(Exception):  # name-based transient detection
    pass


def test_is_transient_by_status_code():
    assert is_transient_error(_StatusError(429)) is True
    assert is_transient_error(_StatusError(503)) is True
    assert is_transient_error(_StatusError(408)) is True
    assert is_transient_error(_StatusError(400)) is False  # bad request -> don't retry
    assert is_transient_error(_StatusError(401)) is False  # auth -> don't retry


def test_is_transient_by_name():
    assert is_transient_error(RateLimitError()) is True
    assert is_transient_error(TimeoutError()) is True
    assert is_transient_error(ValueError("bad json")) is False


class _FlakyInner:
    def __init__(self, fail_times, exc):
        self.fail_times = fail_times
        self.exc = exc
        self.calls = 0

    async def arun(self, prompt):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return type("R", (), {"content": "ok"})()

    def run(self, prompt):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return type("R", (), {"content": "ok"})()


def _agent(inner, **kw):
    return RetryingAgent(inner, wait_multiplier=0.0, **kw)  # no real delay in tests


def test_retries_transient_then_succeeds():
    inner = _FlakyInner(fail_times=2, exc=RateLimitError())
    agent = _agent(inner, max_attempts=3)
    resp = asyncio.run(agent.arun("x"))
    assert resp.content == "ok"
    assert inner.calls == 3  # 2 failures + 1 success


def test_does_not_retry_non_transient():
    inner = _FlakyInner(fail_times=1, exc=ValueError("deterministic"))
    agent = _agent(inner, max_attempts=3)
    with pytest.raises(ValueError):
        asyncio.run(agent.arun("x"))
    assert inner.calls == 1  # not retried


def test_gives_up_after_max_attempts():
    inner = _FlakyInner(fail_times=99, exc=_StatusError(503))
    agent = _agent(inner, max_attempts=3)
    with pytest.raises(_StatusError):
        asyncio.run(agent.arun("x"))
    assert inner.calls == 3  # exhausted attempts, then reraised


def test_sync_run_retries_too():
    inner = _FlakyInner(fail_times=1, exc=RateLimitError())
    agent = _agent(inner, max_attempts=2)
    assert agent.run("x").content == "ok"
    assert inner.calls == 2
