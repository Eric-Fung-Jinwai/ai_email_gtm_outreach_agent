from app import _failure_reasons


def test_failure_reasons_collects_failed_checks_and_judge():
    e = {
        "eval": {
            "checks": [
                {"name": "word_count", "passed": False, "detail": "10 words (allowed 40-200)"},
                {"name": "cta_present", "passed": True, "detail": ""},
            ],
            "judge": {
                "faithful": False,
                "claims": [{"claim": "raised $50M", "grounded": False}],
                "issues": ["unsupported funding claim"],
            },
        }
    }
    reasons = _failure_reasons(e)
    assert any("word_count" in r for r in reasons)
    assert any("raised $50M" in r for r in reasons)
    assert any("unsupported funding claim" in r for r in reasons)
    assert not any("cta_present" in r for r in reasons)  # passed check omitted


def test_failure_reasons_surfaces_prior_judge_after_edit():
    e = {
        "eval": {
            "checks": [],
            "judge": None,
            "judge_stale": True,
            "prior_judge": {"faithful": False, "claims": [{"claim": "London office", "grounded": False}]},
        }
    }
    reasons = _failure_reasons(e)
    assert any("London office" in r for r in reasons)
    assert any("not re-checked" in r for r in reasons)


def test_no_reasons_for_clean_email():
    e = {"eval": {"checks": [{"name": "word_count", "passed": True, "detail": ""}], "judge": {"faithful": True}}}
    assert _failure_reasons(e) == []
