from backend.evaluation.deterministic import _word_count, evaluate_email


def _email(subject="Quick idea for Acme", body=None):
    if body is None:
        # ~60 words, clear CTA, no spam.
        body = (
            "Hi Ada, I noticed Acme is hiring several backend engineers in Chicago, "
            "which usually signals real scaling pressure on the platform team. We help "
            "companies in exactly that spot cut infrastructure toil and ship faster "
            "without growing headcount. I would love to share a couple of concrete "
            "ideas tailored to Acme. Would you be open to a short intro call next week "
            "to discuss whether this is useful for your team?"
        )
    return {"company": "Acme", "contact": "Ada", "subject": subject, "body": body}


def _names(result):
    return {c.name: c for c in result.checks}


def test_good_email_passes_all():
    result = evaluate_email(_email())
    assert result.passed
    assert all(c.passed for c in result.checks)


def test_missing_subject_fails():
    result = evaluate_email(_email(subject="  "))
    assert not result.passed
    assert not _names(result)["subject_present"].passed


def test_empty_body_fails():
    result = evaluate_email(_email(body=""))
    assert not result.passed
    assert not _names(result)["body_present"].passed


def test_too_short_body_fails_word_count():
    result = evaluate_email(_email(body="Hi, let us chat?"))
    assert not _names(result)["word_count"].passed


def test_too_long_body_fails_word_count():
    result = evaluate_email(_email(body="word " * 250))
    assert not _names(result)["word_count"].passed


def test_missing_cta_fails():
    body = "Acme does interesting work in logistics. " * 6  # long enough, no CTA, no '?'
    result = evaluate_email(_email(body=body))
    assert not _names(result)["cta_present"].passed


def test_spam_phrases_flagged():
    body = (
        "Act now! This limited time offer is 100% free and risk-free. "
        "Click here to buy now and claim your cash bonus today, dear friend. "
        "Would you like to chat about it sometime soon with our team?"
    )
    result = evaluate_email(_email(body=body))
    assert not _names(result)["no_spam"].passed
    assert "limited time" in _names(result)["no_spam"].detail


def test_excessive_punctuation_flagged_as_spam():
    result = evaluate_email(_email(body=_email()["body"] + " Amazing deal!!!"))
    assert not _names(result)["no_spam"].passed


def test_calendar_check_only_when_link_provided():
    email = _email()
    # No link provided -> no calendar check present.
    assert "calendar_link_included" not in _names(evaluate_email(email))
    # Link provided but absent from body -> fails.
    r_missing = evaluate_email(email, calendar_link="https://cal.com/ada")
    assert not _names(r_missing)["calendar_link_included"].passed
    # Link present in body -> passes.
    email_with = _email(body=email["body"] + " Grab a time: https://cal.com/ada")
    r_present = evaluate_email(email_with, calendar_link="https://cal.com/ada")
    assert _names(r_present)["calendar_link_included"].passed


def test_word_count_helper_ignores_punctuation():
    assert _word_count("Hello, world! It's a test.") == 5
