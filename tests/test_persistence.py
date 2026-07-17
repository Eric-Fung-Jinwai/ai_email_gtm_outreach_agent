import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from backend.persistence import (
    LOCAL_USER,
    create_user,
    get_or_create_local_user,
    get_run,
    get_user,
    init_db,
    list_runs,
    recently_contacted_companies,
    save_run,
    transition_email,
    update_email_edit,
    user_cost,
    verify_user,
    _set_email_status,
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


def _setup(tmp_path, username="alice"):
    """Return (db_path, user_id) with a fresh DB and one registered user."""
    p = str(tmp_path / "campaigns.db")
    init_db(p)
    uid = create_user(username, "password123", db_path=p)
    return p, uid


# --- users / auth -----------------------------------------------------------

def test_create_and_verify_user(tmp_path):
    db = str(tmp_path / "c.db")
    init_db(db)
    uid = create_user("alice", "s3cretpw", db_path=db)
    assert verify_user("alice", "s3cretpw", db_path=db) == uid
    assert verify_user("alice", "wrongpw", db_path=db) is None      # wrong password
    assert verify_user("nobody", "s3cretpw", db_path=db) is None    # unknown user
    assert get_user(uid, db_path=db)["username"] == "alice"


def test_duplicate_username_rejected(tmp_path):
    db = str(tmp_path / "c.db")
    init_db(db)
    create_user("alice", "pw12345678", db_path=db)
    with pytest.raises(ValueError):
        create_user("alice", "different", db_path=db)


def test_local_user_is_passwordless(tmp_path):
    db = str(tmp_path / "c.db")
    uid = get_or_create_local_user(db_path=db)
    assert get_user(uid, db_path=db)["username"] == LOCAL_USER
    # A passwordless account can never be logged into.
    assert verify_user(LOCAL_USER, "", db_path=db) is None
    # Idempotent resolution by username.
    assert get_or_create_local_user(db_path=db) == uid


# --- round trips (now user-scoped) ------------------------------------------

def test_save_and_get_round_trip(tmp_path):
    db, uid = _setup(tmp_path)
    rid = save_run(_result(), _inputs(), user_id=uid, db_path=db)
    got = get_run(rid, user_id=uid, db_path=db)
    assert got["companies"][0]["name"] == "Acme"
    assert got["research"][0]["insights"][0]["source_type"] == "job_posting"  # evidence preserved
    e = got["emails"][0]
    assert e["id"] == "email-0" and e["status"] == "drafted"
    assert e["eval"]["judge"]["faithful"] is True


def test_list_runs_newest_first(tmp_path):
    db, uid = _setup(tmp_path)
    save_run(_result(), _inputs(), user_id=uid, db_path=db)
    save_run(_result(), _inputs(), user_id=uid, db_path=db)
    runs = list_runs(user_id=uid, db_path=db)
    assert len(runs) == 2
    assert runs[0]["id"] > runs[1]["id"]
    assert runs[0]["n_emails"] == 1 and runs[0]["n_approved"] == 0


def test_update_status_persists(tmp_path):
    db, uid = _setup(tmp_path)
    rid = save_run(_result(), _inputs(), user_id=uid, db_path=db)
    _set_email_status(rid, "email-0", "approved", approved_override=True, db_path=db)
    e = get_run(rid, user_id=uid, db_path=db)["emails"][0]
    assert e["status"] == "approved" and e["approved_override"] is True


def test_update_edit_persists(tmp_path):
    db, uid = _setup(tmp_path)
    rid = save_run(_result(), _inputs(), user_id=uid, db_path=db)
    new_eval = {"passed": True, "ready": False, "checks": [], "judge": None}
    update_email_edit(rid, "email-0", "New subject", "New body", new_eval, "edited", user_id=uid, db_path=db)
    e = get_run(rid, user_id=uid, db_path=db)["emails"][0]
    assert e["subject"] == "New subject" and e["body"] == "New body"
    assert e["status"] == "edited" and e["eval"]["ready"] is False


def test_get_missing_run_returns_none(tmp_path):
    db, uid = _setup(tmp_path)
    assert get_run(999, user_id=uid, db_path=db) is None


def test_lead_score_persisted(tmp_path):
    db, uid = _setup(tmp_path)
    result = _result()
    result["emails"][0]["lead_score"] = 12
    result["emails"][0]["lead_score_breakdown"] = {
        "seniority": 10, "evidence": 1, "ready": 0, "verified_email": 1
    }
    rid = save_run(result, _inputs(), user_id=uid, db_path=db)
    e = get_run(rid, user_id=uid, db_path=db)["emails"][0]
    assert e["lead_score"] == 12
    assert e["lead_score_breakdown"]["seniority"] == 10  # breakdown survives reopen


def test_timings_persisted(tmp_path):
    db, uid = _setup(tmp_path)
    result = _result()
    result["timings"] = {"companies": 0.8, "total": 3.2}
    rid = save_run(result, _inputs(), user_id=uid, db_path=db)
    assert get_run(rid, user_id=uid, db_path=db)["timings"] == {"companies": 0.8, "total": 3.2}


def test_cost_and_breakdown_persisted(tmp_path):
    db, uid = _setup(tmp_path)
    result = _result()
    result["cost"] = 0.1234
    result["cost_breakdown"] = {
        "judge": {"model": "gpt-4o", "input_tokens": 10, "output_tokens": 5, "cost": 0.001}
    }
    rid = save_run(result, _inputs(), user_id=uid, db_path=db)
    got = get_run(rid, user_id=uid, db_path=db)
    assert got["cost"] == 0.1234
    assert got["cost_breakdown"]["judge"]["model"] == "gpt-4o"  # per-stage detail survives reopen
    assert list_runs(user_id=uid, db_path=db)[0]["cost"] == 0.1234


def test_user_cost_sums_runs(tmp_path):
    db, uid = _setup(tmp_path)
    assert user_cost(user_id=uid, db_path=db) == 0.0  # COALESCE → 0 for a new user
    for c in (0.01, 0.02):
        r = _result()
        r["cost"] = c
        save_run(r, _inputs(), user_id=uid, db_path=db)
    assert round(user_cost(user_id=uid, db_path=db), 4) == 0.03


def test_edit_clears_stale_override(tmp_path):
    db, uid = _setup(tmp_path)
    rid = save_run(_result(), _inputs(), user_id=uid, db_path=db)
    _set_email_status(rid, "email-0", "approved", approved_override=True, db_path=db)
    assert get_run(rid, user_id=uid, db_path=db)["emails"][0]["approved_override"] is True
    update_email_edit(rid, "email-0", "s", "b", {"passed": True, "ready": False}, "edited", user_id=uid, db_path=db)
    # A new version must not carry the old override flag.
    assert get_run(rid, user_id=uid, db_path=db)["emails"][0]["approved_override"] is False


def test_transition_enforces_state_machine(tmp_path):
    db, uid = _setup(tmp_path)
    rid = save_run(_result(), _inputs(), user_id=uid, db_path=db)  # email-0 drafted, ready=True
    res = transition_email(rid, "email-0", "approve", user_id=uid, db_path=db)
    assert res["status"] == "approved"
    assert res["approved_override"] is False  # ready draft -> not an override
    # Approving an already-approved draft is illegal.
    with pytest.raises(ValueError):
        transition_email(rid, "email-0", "approve", user_id=uid, db_path=db)
    assert get_run(rid, user_id=uid, db_path=db)["emails"][0]["status"] == "approved"


def test_transition_records_override_for_not_ready(tmp_path):
    db, uid = _setup(tmp_path)
    result = _result()
    result["emails"][0]["eval"]["ready"] = False  # not ready
    rid = save_run(result, _inputs(), user_id=uid, db_path=db)
    res = transition_email(rid, "email-0", "approve", user_id=uid, db_path=db)
    assert res["status"] == "approved" and res["approved_override"] is True  # recorded override
    assert get_run(rid, user_id=uid, db_path=db)["emails"][0]["approved_override"] is True


def test_transition_conflict_raises(tmp_path, monkeypatch):
    # Simulate a concurrent tab moving the row between our read and conditional
    # write: the WHERE status=<read> matches 0 rows → ConcurrencyConflict (not a
    # silent last-write-wins).
    import backend.persistence as P

    db, uid = _setup(tmp_path)
    rid = save_run(_result(), _inputs(), user_id=uid, db_path=db)
    real_apply = P.apply_action

    def racing(status, action):
        with sqlite3.connect(db) as c:  # another writer commits first
            c.execute("UPDATE emails SET status='rejected' WHERE run_id=? AND email_id=?", (rid, "email-0"))
        return real_apply(status, action)

    monkeypatch.setattr(P, "apply_action", racing)
    with pytest.raises(P.ConcurrencyConflict):
        transition_email(rid, "email-0", "approve", user_id=uid, db_path=db)


def test_version_increments_and_is_returned(tmp_path):
    db, uid = _setup(tmp_path)
    rid = save_run(_result(), _inputs(), user_id=uid, db_path=db)
    assert get_run(rid, user_id=uid, db_path=db)["emails"][0]["version"] == 0
    res = transition_email(rid, "email-0", "approve", user_id=uid, db_path=db)
    assert res["version"] == 1
    assert get_run(rid, user_id=uid, db_path=db)["emails"][0]["version"] == 1


def test_edit_optimistic_conflict(tmp_path):
    import backend.persistence as P

    db, uid = _setup(tmp_path)
    rid = save_run(_result(), _inputs(), user_id=uid, db_path=db)
    # First edit against the version we saw (0) → succeeds, bumps to 1.
    v = update_email_edit(rid, "email-0", "s1", "b1", {"ready": True}, "edited",
                          user_id=uid, expected_version=0, db_path=db)
    assert v == 1
    # A second tab still holding version 0 must be refused, not clobber v1.
    with pytest.raises(P.ConcurrencyConflict):
        update_email_edit(rid, "email-0", "s2", "b2", {"ready": True}, "edited",
                          user_id=uid, expected_version=0, db_path=db)
    assert get_run(rid, user_id=uid, db_path=db)["emails"][0]["subject"] == "s1"


def test_transition_version_conflict(tmp_path):
    import backend.persistence as P

    db, uid = _setup(tmp_path)
    rid = save_run(_result(), _inputs(), user_id=uid, db_path=db)
    with pytest.raises(P.ConcurrencyConflict):
        transition_email(rid, "email-0", "approve", user_id=uid, expected_version=99, db_path=db)


def test_transition_unknown_email_raises(tmp_path):
    db, uid = _setup(tmp_path)
    save_run(_result(), _inputs(), user_id=uid, db_path=db)
    with pytest.raises(LookupError):
        transition_email(999, "email-0", "approve", user_id=uid, db_path=db)


def test_update_raises_on_unknown_email(tmp_path):
    db, uid = _setup(tmp_path)
    rid = save_run(_result(), _inputs(), user_id=uid, db_path=db)
    with pytest.raises(LookupError):
        _set_email_status(rid, "email-999", "approved", db_path=db)
    with pytest.raises(LookupError):
        update_email_edit(rid, "email-999", "s", "b", {"ready": True}, "edited", user_id=uid, db_path=db)


# --- tenant isolation -------------------------------------------------------

def test_tenant_isolation(tmp_path):
    db, alice = _setup(tmp_path, "alice")
    bob = create_user("bob", "password123", db_path=db)
    rid = save_run(_result(), _inputs(), user_id=alice, db_path=db)

    # Bob cannot see, reopen, transition, or edit Alice's run.
    assert list_runs(user_id=bob, db_path=db) == []
    assert get_run(rid, user_id=bob, db_path=db) is None
    with pytest.raises(LookupError):
        transition_email(rid, "email-0", "approve", user_id=bob, db_path=db)
    with pytest.raises(LookupError):
        update_email_edit(rid, "email-0", "s", "b", {"ready": True}, "edited", user_id=bob, db_path=db)

    # Alice still has full access, and Bob's tamper attempts changed nothing.
    assert get_run(rid, user_id=alice, db_path=db)["emails"][0]["status"] == "drafted"


# --- cooldown scoping -------------------------------------------------------

def test_recently_contacted_within_window(tmp_path):
    db, uid = _setup(tmp_path)
    save_run(_result(), _inputs(), user_id=uid, db_path=db)
    assert "Acme" in recently_contacted_companies(30, user_id=uid, db_path=db)
    assert recently_contacted_companies(0, user_id=uid, db_path=db) == []  # disabled


def test_cooldown_scope_user_vs_global(tmp_path):
    db, alice = _setup(tmp_path, "alice")
    bob = create_user("bob", "password123", db_path=db)
    save_run(_result(), _inputs(), user_id=alice, db_path=db)  # Alice contacted Acme
    # Per-user: Bob's own history is empty; global: sees Alice's contact.
    assert recently_contacted_companies(30, user_id=bob, db_path=db) == []
    assert "Acme" in recently_contacted_companies(30, user_id=None, db_path=db)


def test_recently_contacted_excludes_old_runs(tmp_path):
    db, uid = _setup(tmp_path)
    rid = save_run(_result(), _inputs(), user_id=uid, db_path=db)
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    conn = sqlite3.connect(db)
    conn.execute("UPDATE runs SET created_at = ? WHERE id = ?", (old, rid))
    conn.commit()
    conn.close()
    # 60 days ago is outside a 30-day cooldown -> eligible again.
    assert "Acme" not in recently_contacted_companies(30, user_id=uid, db_path=db)


def test_recently_contacted_missing_db_returns_empty(tmp_path):
    assert recently_contacted_companies(30, db_path=str(tmp_path / "nope.db")) == []


# --- migration + pragmas ----------------------------------------------------

def test_pragmas_enabled(tmp_path):
    from backend.persistence import _connect

    db = str(tmp_path / "c.db")
    init_db(db)
    # _connect must enable foreign keys per connection and put the DB in WAL mode.
    with _connect(db) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_init_db_migrates_and_backfills_user_id(tmp_path):
    # Simulate a pre-multi-user DB: runs/emails without user_id, one existing run.
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, snapshot_json TEXT);"
        "CREATE TABLE emails (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, email_id TEXT, status TEXT);"
    )
    conn.execute(
        "INSERT INTO runs (created_at, snapshot_json) VALUES (?, ?)",
        (datetime.now(timezone.utc).isoformat(), '{"companies": [], "contacts": [], "research": []}'),
    )
    conn.commit()
    conn.close()

    init_db(db)  # migrate + backfill
    cols = {row[1] for row in sqlite3.connect(db).execute("PRAGMA table_info(runs)")}
    assert "user_id" in cols
    # The orphaned run is now owned by LOCAL_USER and reachable.
    local_id = get_or_create_local_user(db_path=db)
    runs = list_runs(user_id=local_id, db_path=db)
    assert len(runs) == 1
    # And the store is fully usable after migration.
    rid = save_run(_result(), _inputs(), user_id=local_id, db_path=db)
    assert get_run(rid, user_id=local_id, db_path=db)["emails"][0]["id"] == "email-0"


def test_save_run_autocreates_schema(tmp_path):
    # save_run should work without an explicit init_db call.
    db = str(tmp_path / "fresh.db")
    uid = get_or_create_local_user(db_path=db)
    rid = save_run(_result(), _inputs(), user_id=uid, db_path=db)
    assert get_run(rid, user_id=uid, db_path=db)["emails"][0]["id"] == "email-0"
