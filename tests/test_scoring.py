from backend.scoring import lead_score, seniority_score


def test_seniority_tiers():
    assert seniority_score("Chief Executive Officer") == 5
    assert seniority_score("Founder & CEO") == 5
    assert seniority_score("President") == 5
    assert seniority_score("VP of Sales") == 4
    assert seniority_score("Vice President, Marketing") == 4  # not misread as C-level
    assert seniority_score("Head of Growth") == 4
    assert seniority_score("Director of Ops") == 3
    assert seniority_score("Engineering Manager") == 2
    assert seniority_score("Software Engineer") == 1
    assert seniority_score("") == 1


def test_lead_score_rewards_seniority_evidence_and_readiness():
    strong = lead_score(seniority=5, insight_count=4, has_job_evidence=True, ready=True, inferred=False)
    weak = lead_score(seniority=1, insight_count=0, has_job_evidence=False, ready=False, inferred=True)
    assert strong["score"] > weak["score"]
    assert strong["breakdown"]["seniority"] == 10
    assert strong["breakdown"]["evidence"] == 7  # min(4,4) + 3 for job evidence
    assert strong["breakdown"]["ready"] == 3
    assert strong["breakdown"]["verified_email"] == 1
    assert weak["breakdown"] == {"seniority": 2, "evidence": 0, "ready": 0, "verified_email": 0}


def test_insight_count_is_capped():
    a = lead_score(seniority=1, insight_count=100, has_job_evidence=False, ready=False, inferred=True)
    b = lead_score(seniority=1, insight_count=4, has_job_evidence=False, ready=False, inferred=True)
    assert a["breakdown"]["evidence"] == b["breakdown"]["evidence"] == 4
