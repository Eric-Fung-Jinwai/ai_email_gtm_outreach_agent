import sqlite3

import pytest

from backend.persistence import (
    get_run,
    init_db,
    list_runs,
    save_run,
    update_email_edit,
    update_email_status,
)


def _result():
    return {
        "companies": [{"name": "Acme", "website": "acme.com", "why_fit": "fit"}],
        "contacts": [{"name": "Acme", "contacts": [{"full_name": "Ada", "email": "a@acme.com"}]}],
        "research": [{"name": "Acme", "insights": [{"text": "hiring", "source_type": "job_posting"}]}],
        "emails": [
            {
                "id": "email-0",
                "company": "Acme",
                "contact": "Ada",
                "subject": "Hi",
                "body": "Body",
                "status": "drafted",
                "eval": {"passed": True, "ready": True, "checks": [], "judge": {"faithful": True}},
            }
        ],
        "calendar_link": None,
    }


def _inputs():
    return {
        "target_desc": "t", "offering_desc": "o", "sender_name": "S",
        "sender_company": "C", "num_companies": 1, "email_style": "Professional",
    }


def _db(tmp_path):
    p = str(tmp_path / "campaigns.db")
    init_db(p)
    return p


def test_save_and_get_round_trip(tmp_path):
    db = _db(tmp_path)
    rid = save_run(_result(), _inputs(), db_path=db)
    got = get_run(rid, db_path=db)
    assert got["companies"][0]["name"] == "Acme"
    assert got["research"][0]["insights"][0]["source_type"] == "job_posting"  # evidence preserved
    e = got["emails"][0]
    assert e["id"] == "email-0" and e["status"] == "drafted"
    assert e["eval"]["judge"]["faithful"] is True


def test_list_runs_newest_first(tmp_path):
    db = _db(tmp_path)
    save_run(_result(), _inputs(), db_path=db)
    save_run(_result(), _inputs(), db_path=db)
    runs = list_runs(db_path=db)
    assert len(runs) == 2
    assert runs[0]["id"] > runs[1]["id"]
    assert runs[0]["n_emails"] == 1 and runs[0]["n_approved"] == 0


def test_update_status_persists(tmp_path):
    db = _db(tmp_path)
    rid = save_run(_result(), _inputs(), db_path=db)
    update_email_status(rid, "email-0", "approved", approved_override=True, db_path=db)
    e = get_run(rid, db_path=db)["emails"][0]
    assert e["status"] == "approved" and e["approved_override"] is True


def test_update_edit_persists(tmp_path):
    db = _db(tmp_path)
    rid = save_run(_result(), _inputs(), db_path=db)
    new_eval = {"passed": True, "ready": False, "checks": [], "judge": None}
    update_email_edit(rid, "email-0", "New subject", "New body", new_eval, "edited", db_path=db)
    e = get_run(rid, db_path=db)["emails"][0]
    assert e["subject"] == "New subject" and e["body"] == "New body"
    assert e["status"] == "edited" and e["eval"]["ready"] is False


def test_get_missing_run_returns_none(tmp_path):
    db = _db(tmp_path)
    assert get_run(999, db_path=db) is None


def test_edit_clears_stale_override(tmp_path):
    db = _db(tmp_path)
    rid = save_run(_result(), _inputs(), db_path=db)
    update_email_status(rid, "email-0", "approved", approved_override=True, db_path=db)
    assert get_run(rid, db_path=db)["emails"][0]["approved_override"] is True
    update_email_edit(rid, "email-0", "s", "b", {"passed": True, "ready": False}, "edited", db_path=db)
    # A new version must not carry the old override flag.
    assert get_run(rid, db_path=db)["emails"][0]["approved_override"] is False


def test_update_raises_on_unknown_email(tmp_path):
    db = _db(tmp_path)
    rid = save_run(_result(), _inputs(), db_path=db)
    with pytest.raises(LookupError):
        update_email_status(rid, "email-999", "approved", db_path=db)
    with pytest.raises(LookupError):
        update_email_edit(rid, "email-999", "s", "b", {"ready": True}, "edited", db_path=db)


def test_init_db_migrates_missing_columns(tmp_path):
    # Simulate an older DB missing newer columns; init_db must add them.
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, snapshot_json TEXT);"
        "CREATE TABLE emails (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, email_id TEXT, status TEXT);"
    )
    conn.commit()
    conn.close()

    init_db(db)  # migrate
    cols = {row[1] for row in sqlite3.connect(db).execute("PRAGMA table_info(emails)")}
    assert {"approved_override", "eval_json", "ready", "faithful", "subject"} <= cols
    # And the store is fully usable after migration.
    rid = save_run(_result(), _inputs(), db_path=db)
    assert get_run(rid, db_path=db)["emails"][0]["id"] == "email-0"


def test_save_run_autocreates_schema(tmp_path):
    # save_run should work without an explicit init_db call.
    db = str(tmp_path / "fresh.db")
    rid = save_run(_result(), _inputs(), db_path=db)
    assert get_run(rid, db_path=db)["emails"][0]["id"] == "email-0"
