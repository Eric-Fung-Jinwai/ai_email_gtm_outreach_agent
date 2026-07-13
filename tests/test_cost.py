import asyncio

from backend.cost import CostTracker, MeteringAgent, cost_for, extract_usage


class _Resp:
    def __init__(self, metrics=None, usage=None, content="{}"):
        self.content = content
        if metrics is not None:
            self.metrics = metrics
        if usage is not None:
            self.usage = usage


def test_extract_usage_from_object_metrics():
    class M:
        input_tokens = 100
        output_tokens = 50

    assert extract_usage(_Resp(metrics=M())) == (100, 50)


def test_extract_usage_from_dict_metrics():
    assert extract_usage(_Resp(metrics={"input_tokens": 10, "output_tokens": 5})) == (10, 5)


def test_extract_usage_openai_style_usage():
    assert extract_usage(_Resp(usage={"prompt_tokens": 7, "completion_tokens": 3})) == (7, 3)


def test_extract_usage_sums_token_lists():
    assert extract_usage(_Resp(metrics={"input_tokens": [10, 20], "output_tokens": [5]})) == (30, 5)


def test_extract_usage_missing_returns_zero():
    assert extract_usage(_Resp()) == (0, 0)


def test_cost_for_known_and_unknown_models():
    assert cost_for("gpt-4o", 1_000_000, 0) == 2.5
    assert cost_for("gpt-4o", 0, 1_000_000) == 10.0
    assert cost_for("mystery-model", 1_000_000, 0) == 1.0  # default fallback, not free


def test_cost_tracker_keeps_same_model_stages_separate():
    t = CostTracker()
    # contact_finder and judge share gpt-4o but must NOT merge into one line.
    t.record("contact_finder", "gpt-4o", _Resp(metrics={"input_tokens": 1_000_000, "output_tokens": 0}))
    t.record("judge", "gpt-4o", _Resp(metrics={"input_tokens": 0, "output_tokens": 1_000_000}))
    t.record("company_finder", "gpt-5.4-nano", _Resp(metrics={"input_tokens": 1_000_000, "output_tokens": 0}))
    assert t.total_tokens() == 3_000_000
    b = t.breakdown()
    assert set(b) == {"contact_finder", "judge", "company_finder"}
    assert b["contact_finder"]["cost"] == 2.5 and b["judge"]["cost"] == 10.0
    assert b["contact_finder"]["model"] == "gpt-4o" and b["judge"]["model"] == "gpt-4o"
    assert t.total_cost() == round(2.5 + 10.0 + 0.05, 6)


def test_metering_agent_records_run_and_arun():
    t = CostTracker()

    class _Inner:
        async def arun(self, prompt):
            return _Resp(metrics={"input_tokens": 100, "output_tokens": 50})

        def run(self, prompt):
            return _Resp(metrics={"input_tokens": 10, "output_tokens": 5})

    m = MeteringAgent(_Inner(), "email_writer", "gpt-4o", t)
    asyncio.run(m.arun("x"))
    m.run("y")
    assert t.total_tokens() == 165
    assert t.breakdown()["email_writer"]["output_tokens"] == 55
    assert t.total_cost() > 0
