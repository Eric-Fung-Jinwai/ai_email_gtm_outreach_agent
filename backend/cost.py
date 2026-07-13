"""LLM cost / token tracking (Phase 9).

A ``MeteringAgent`` wraps an agent and records the token usage of every call into
a ``CostTracker``, priced via a static table — so cost is captured transparently
without touching the pipeline logic, and the (paid) judge's cost shows up as its
own line, making the "gate the judge to bound cost" story concrete.

Prices are ESTIMATES (USD per 1M tokens) — update ``PRICING`` to your account's
actual rates. Unknown models fall back to a conservative default so a real call
is never silently free.
"""

from typing import Any, Dict, Tuple

# USD per 1M tokens: (input, output). Estimates — adjust to real rates.
PRICING: Dict[str, Tuple[float, float]] = {
    "gpt-4o": (2.5, 10.0),
    "gpt-5.4-nano": (0.05, 0.40),
}
_DEFAULT_PRICE = (1.0, 3.0)


def _num(v: Any) -> float:
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, list):  # agno may expose per-message token lists
        return float(sum(_num(x) for x in v))
    return 0.0


def _attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def extract_usage(resp: Any) -> Tuple[int, int]:
    """Best-effort (input_tokens, output_tokens) from an agent response.

    Handles agno-style ``metrics`` and OpenAI-style ``usage``; returns (0, 0) for
    fakes or when usage is unavailable, so it never raises.
    """
    for holder_name in ("metrics", "usage"):
        holder = _attr(resp, holder_name)
        if holder is None:
            continue
        it = _num(_attr(holder, "input_tokens")) or _num(_attr(holder, "prompt_tokens"))
        ot = _num(_attr(holder, "output_tokens")) or _num(_attr(holder, "completion_tokens"))
        if it or ot:
            return int(it), int(ot)
    return 0, 0


def cost_for(model_id: str, input_tokens: int, output_tokens: int) -> float:
    in_rate, out_rate = PRICING.get(model_id, _DEFAULT_PRICE)
    return input_tokens / 1_000_000 * in_rate + output_tokens / 1_000_000 * out_rate


class CostTracker:
    """Accumulates token usage per **stage** (company_finder, contact_finder,
    researcher, email_writer, judge). Grouping by stage — not model — keeps
    distinct stages separate even when they share a model id (e.g. contact_finder
    and judge both default to gpt-4o)."""

    def __init__(self) -> None:
        self._by_stage: Dict[str, Dict[str, Any]] = {}

    def record(self, stage: str, model_id: str, resp: Any) -> None:
        it, ot = extract_usage(resp)
        acc = self._by_stage.setdefault(stage, {"model": model_id, "input": 0, "output": 0})
        acc["model"] = model_id
        acc["input"] += it
        acc["output"] += ot

    def total_tokens(self) -> int:
        return sum(a["input"] + a["output"] for a in self._by_stage.values())

    def total_cost(self) -> float:
        return round(
            sum(cost_for(a["model"], a["input"], a["output"]) for a in self._by_stage.values()), 6
        )

    def breakdown(self) -> Dict[str, Dict[str, Any]]:
        return {
            stage: {
                "model": a["model"],
                "input_tokens": a["input"],
                "output_tokens": a["output"],
                "cost": round(cost_for(a["model"], a["input"], a["output"]), 6),
            }
            for stage, a in self._by_stage.items()
        }


class MeteringAgent:
    """Delegates to an agent, recording token usage of every run/arun call under a
    named ``stage``."""

    def __init__(self, inner: Any, stage: str, model_id: str, tracker: CostTracker) -> None:
        self._inner = inner
        self._stage = stage
        self._model_id = model_id
        self._tracker = tracker

    def run(self, prompt: str) -> Any:
        resp = self._inner.run(prompt)
        self._tracker.record(self._stage, self._model_id, resp)
        return resp

    async def arun(self, prompt: str) -> Any:
        resp = await self._inner.arun(prompt)
        self._tracker.record(self._stage, self._model_id, resp)
        return resp
