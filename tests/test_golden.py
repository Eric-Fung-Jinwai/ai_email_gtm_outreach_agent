import asyncio
import json

from backend.evaluation.golden import format_report, load_golden, run_agreement
from backend.models import GoldenExample


def _ex(id_, body, label):
    return GoldenExample(id=id_, email={"subject": "s", "body": body}, evidence=[], label_faithful=label)


class _MarkerJudge:
    """Predicts faithful iff the prompt contains FAITHFUL_MARKER (so tests control it)."""

    async def arun(self, prompt):
        faithful = "FAITHFUL_MARKER" in prompt
        return type("R", (), {"content": json.dumps({"faithful": faithful, "claims": []})})()

    def run(self, prompt):
        return None


def test_golden_set_is_well_formed():
    examples = load_golden()
    assert len(examples) >= 10
    ids = [e.id for e in examples]
    assert len(ids) == len(set(ids))  # unique ids
    # Both classes represented, so the harness can measure precision AND recall.
    assert any(e.label_faithful for e in examples)
    assert any(not e.label_faithful for e in examples)
    # Every example is parseable into typed models.
    assert all(e.email.body for e in examples)


def test_run_agreement_metrics_are_correct():
    examples = [
        _ex("a", "FAITHFUL_MARKER present", True),   # pred T, actual T -> agree (TN)
        _ex("b", "no marker here", False),           # pred F, actual F -> agree (TP)
        _ex("c", "FAITHFUL_MARKER present", False),  # pred T, actual F -> miss   (FN)
        _ex("d", "no marker here", True),            # pred F, actual T -> false flag (FP)
    ]
    r = asyncio.run(run_agreement(examples, _MarkerJudge()))
    assert r.total == 4 and r.evaluated == 4 and r.errors == 0
    assert r.agreements == 2 and r.accuracy == 0.5
    assert (r.true_pos, r.false_pos, r.false_neg, r.true_neg) == (1, 1, 1, 1)
    assert r.precision == 0.5 and r.recall == 0.5


def test_all_faithful_judge_has_zero_recall():
    class _AlwaysFaithful:
        async def arun(self, prompt):
            return type("R", (), {"content": json.dumps({"faithful": True})})()

        def run(self, prompt):
            return None

    examples = [_ex("a", "x", True), _ex("b", "y", False), _ex("c", "z", False)]
    r = asyncio.run(run_agreement(examples, _AlwaysFaithful()))
    assert r.false_neg == 2  # both unfaithful missed
    assert r.recall == 0.0


def test_run_agreement_counts_judge_errors():
    class _Boom:
        async def arun(self, prompt):
            raise RuntimeError("model down")

        def run(self, prompt):
            return None

    r = asyncio.run(run_agreement([_ex("a", "x", True)], _Boom()))
    assert r.errors == 1 and r.evaluated == 0 and r.accuracy == 0.0


def test_format_report_runs():
    r = asyncio.run(run_agreement([_ex("a", "FAITHFUL_MARKER", True)], _MarkerJudge()))
    out = format_report(r)
    assert "accuracy" in out and "precision" in out
