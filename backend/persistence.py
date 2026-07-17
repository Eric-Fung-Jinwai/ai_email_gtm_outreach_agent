"""Campaign persistence (Phase 6) — a SEPARATE application database.

This is deliberately NOT the agno agent-memory DB (which was removed in Phase 3).
It stores *application* records with our own schema so runs and approval statuses
survive a refresh / new session.

Schema:
- ``runs``   — one row per outreach run; immutable snapshot (companies/contacts/
  research) as JSON, plus the inputs and an optional cost (Phase 9).
- ``emails`` — one row per generated email, normalized so status/edits update in
  place. Emails are the *only* mutable records; companies/contacts/research live
  in the run snapshot and never change post-run (so nothing can diverge).

Threading: all writes happen synchronously on the caller's thread. The pipeline
fans out on worker threads but returns results; persistence runs on the main
thread AFTER the join, so SQLite's single-writer never sees concurrent writes.
"""

import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.approval import apply_action
from backend.config import get_settings

# Deterministic legacy account. Single-user data (pre-multi-user, and the Streamlit
# app) is owned by this user so nothing becomes orphaned once queries require a
# user_id. Resolved by *username* — never by a hardcoded row id.
LOCAL_USER = "local"


class ConcurrencyConflict(Exception):
    """Raised when an approval transition loses a race — the row's status changed
    between our read and our conditional write (e.g. two browser tabs). Callers map
    this to HTTP 409 so the client can reload and retry."""

# Password hashing (stdlib; no third-party crypto dep).
_PBKDF2_ALGO = "sha256"
_PBKDF2_ITERATIONS = 600_000


def _resolve_path(db_path: Optional[str]) -> str:
    return db_path or get_settings().app_db_path


@contextmanager
def _connect(db_path: Optional[str] = None):
    path = _resolve_path(db_path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Concurrency/integrity policy: WAL lets readers proceed during a write;
    # busy_timeout lets a briefly-contended writer wait instead of erroring; and
    # foreign_keys must be enabled per connection for the emails->runs cascade.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Non-PK columns we expect on each table, with their ALTER-ADD declaration.
# Used to additively migrate an older DB (new columns are added, never dropped).
_EXPECTED_COLUMNS = {
    "runs": {
        "created_at": "TEXT", "target_desc": "TEXT", "offering_desc": "TEXT",
        "sender_name": "TEXT", "sender_company": "TEXT", "calendar_link": "TEXT",
        "num_companies": "INTEGER", "email_style": "TEXT", "snapshot_json": "TEXT",
        "cost": "REAL", "cost_breakdown_json": "TEXT",
        "user_id": "INTEGER",  # multi-user: owner; backfilled to LOCAL_USER on migrate
    },
    "emails": {
        "run_id": "INTEGER", "email_id": "TEXT", "company": "TEXT", "contact": "TEXT",
        "subject": "TEXT", "body": "TEXT", "status": "TEXT", "ready": "INTEGER",
        "faithful": "INTEGER", "approved_override": "INTEGER DEFAULT 0", "eval_json": "TEXT",
        "lead_score": "INTEGER", "lead_score_breakdown_json": "TEXT",
        "version": "INTEGER NOT NULL DEFAULT 0",  # optimistic-lock counter
    },
}


def _migrate(conn) -> None:
    """Add any expected columns missing from an existing (older) schema, then
    backfill ownership so older single-user rows stay reachable once queries
    require a user_id."""
    added_user_id = False
    for table, columns in _EXPECTED_COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                if table == "runs" and name == "user_id":
                    added_user_id = True
    # Any run without an owner (pre-multi-user data, or the legacy Streamlit app)
    # is assigned to the deterministic LOCAL_USER, resolved by username.
    orphaned = conn.execute("SELECT COUNT(*) AS n FROM runs WHERE user_id IS NULL").fetchone()
    if added_user_id or (orphaned and orphaned["n"]):
        local_id = _get_or_create_user(conn, LOCAL_USER)
        conn.execute("UPDATE runs SET user_id = ? WHERE user_id IS NULL", (local_id,))


def init_db(db_path: Optional[str] = None) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT,
                salt TEXT,
                iterations INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                target_desc TEXT,
                offering_desc TEXT,
                sender_name TEXT,
                sender_company TEXT,
                calendar_link TEXT,
                num_companies INTEGER,
                email_style TEXT,
                snapshot_json TEXT NOT NULL,
                cost REAL,
                cost_breakdown_json TEXT,
                user_id INTEGER REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                email_id TEXT NOT NULL,
                company TEXT,
                contact TEXT,
                subject TEXT,
                body TEXT,
                status TEXT NOT NULL,
                ready INTEGER,
                faithful INTEGER,
                approved_override INTEGER DEFAULT 0,
                eval_json TEXT,
                lead_score INTEGER,
                lead_score_breakdown_json TEXT,
                version INTEGER NOT NULL DEFAULT 0,
                UNIQUE(run_id, email_id)
            );
            """
        )
        _migrate(conn)


# --- Users / auth (multi-user) ----------------------------------------------

def _hash_password(password: str, salt: bytes, iterations: int = _PBKDF2_ITERATIONS) -> str:
    dk = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, password.encode("utf-8"), salt, iterations)
    return dk.hex()


def _get_or_create_user(conn, username: str, password: Optional[str] = None) -> int:
    """Return the id for ``username``, creating the row if absent. Used both for
    the passwordless LOCAL_USER (migration/Streamlit) and, with a password, for
    registration. Must run inside an open connection so it shares the txn."""
    row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if row is not None:
        return int(row["id"])
    salt = secrets.token_bytes(16)
    pw_hash = _hash_password(password, salt) if password is not None else None
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, salt, iterations, created_at) "
        "VALUES (?,?,?,?,?)",
        (
            username,
            pw_hash,
            salt.hex() if password is not None else None,
            _PBKDF2_ITERATIONS if password is not None else None,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    return int(cur.lastrowid)


# A fixed dummy salt so verify_user can hash even for a nonexistent user, keeping
# the work (and timing) similar whether or not the username exists.
_DUMMY_SALT = b"\x00" * 16


def create_user(username: str, password: str, db_path: Optional[str] = None) -> int:
    """Register a new user. Raises ``ValueError`` if the username is taken.

    The pre-check is best-effort; the ``UNIQUE(username)`` constraint is the real
    guard, so a concurrent double-registration surfaces as ``ValueError`` (mapped to
    409) rather than an unhandled ``IntegrityError``/500 (High #6)."""
    init_db(db_path)
    try:
        with _connect(db_path) as conn:
            exists = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
            if exists is not None:
                raise ValueError("username already taken")
            return _get_or_create_user(conn, username, password)
    except sqlite3.IntegrityError:  # lost the race to another concurrent register
        raise ValueError("username already taken")


def verify_user(username: str, password: str, db_path: Optional[str] = None) -> Optional[int]:
    """Return the user id iff credentials match, else ``None``.

    Always performs a PBKDF2 hash — with a dummy salt when the user is absent or
    passwordless (e.g. LOCAL_USER) — so an attacker can't distinguish existing from
    non-existing usernames by timing (High #5). ``hmac.compare_digest`` is used for
    the real comparison. Tolerant of a not-yet-created schema (no users table ⇒ no
    such user)."""
    try:
        with _connect(db_path) as conn:
            row = conn.execute(
                "SELECT id, password_hash, salt, iterations FROM users WHERE username = ?",
                (username,),
            ).fetchone()
    except sqlite3.OperationalError:
        row = None  # tables not created yet
    if row is None or not row["password_hash"] or not row["salt"]:
        _hash_password(password, _DUMMY_SALT)  # equalize timing; result discarded
        return None
    candidate = _hash_password(password, bytes.fromhex(row["salt"]), int(row["iterations"]))
    if hmac.compare_digest(candidate, row["password_hash"]):
        return int(row["id"])
    return None


def get_user(user_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, username, created_at FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def get_or_create_local_user(db_path: Optional[str] = None) -> int:
    """Resolve the deterministic LOCAL_USER id (for the legacy Streamlit app)."""
    init_db(db_path)
    with _connect(db_path) as conn:
        return _get_or_create_user(conn, LOCAL_USER)


def save_run(
    result: Dict[str, Any], inputs: Dict[str, Any], *, user_id: int, db_path: Optional[str] = None
) -> int:
    """Persist a completed run and its emails for ``user_id``. Returns the run id."""
    init_db(db_path)
    snapshot = {
        "companies": result.get("companies", []),
        "contacts": result.get("contacts", []),
        "research": result.get("research", []),
        "calendar_link": result.get("calendar_link"),
        "timings": result.get("timings"),  # per-stage observability (immutable)
    }
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO runs (created_at, target_desc, offering_desc, sender_name, "
            "sender_company, calendar_link, num_companies, email_style, snapshot_json, "
            "cost, cost_breakdown_json, user_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(),
                inputs.get("target_desc"),
                inputs.get("offering_desc"),
                inputs.get("sender_name"),
                inputs.get("sender_company"),
                result.get("calendar_link"),
                inputs.get("num_companies"),
                inputs.get("email_style"),
                json.dumps(snapshot),
                result.get("cost"),  # Phase 9: estimated LLM cost
                json.dumps(result.get("cost_breakdown") or {}),
                user_id,
            ),
        )
        run_id = int(cur.lastrowid)
        for e in result.get("emails", []):
            ev = e.get("eval") or {}
            faithful = (ev.get("judge") or {}).get("faithful")
            conn.execute(
                "INSERT INTO emails (run_id, email_id, company, contact, subject, body, "
                "status, ready, faithful, approved_override, eval_json, lead_score, "
                "lead_score_breakdown_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    e.get("id"),
                    e.get("company"),
                    e.get("contact"),
                    e.get("subject"),
                    e.get("body"),
                    e.get("status", "drafted"),
                    int(bool(ev.get("ready"))),
                    None if faithful is None else int(faithful),
                    int(bool(e.get("approved_override"))),
                    json.dumps(ev),
                    e.get("lead_score"),
                    json.dumps(e.get("lead_score_breakdown") or {}),
                ),
            )
    return run_id


def list_runs(*, user_id: int, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Summaries of ``user_id``'s runs, newest first."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT r.id, r.created_at, r.target_desc, r.cost, COUNT(e.id) AS n_emails, "
            "SUM(CASE WHEN e.status='approved' THEN 1 ELSE 0 END) AS n_approved "
            "FROM runs r LEFT JOIN emails e ON e.run_id = r.id "
            "WHERE r.user_id = ? "
            "GROUP BY r.id ORDER BY r.id DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def user_cost(*, user_id: int, db_path: Optional[str] = None) -> float:
    """Total estimated LLM spend attributed to ``user_id`` across all runs."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost), 0) AS total FROM runs WHERE user_id = ?", (user_id,)
        ).fetchone()
        return float(row["total"])


def get_run(run_id: int, *, user_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Reconstruct ``user_id``'s run into the pipeline's shape (for reopening).
    Returns ``None`` if the run doesn't exist OR belongs to another user."""
    with _connect(db_path) as conn:
        run = conn.execute(
            "SELECT * FROM runs WHERE id = ? AND user_id = ?", (run_id, user_id)
        ).fetchone()
        if run is None:
            return None
        snapshot = json.loads(run["snapshot_json"])
        emails = []
        for e in conn.execute(
            "SELECT * FROM emails WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall():
            emails.append(
                {
                    "id": e["email_id"],
                    "company": e["company"],
                    "contact": e["contact"],
                    "subject": e["subject"],
                    "body": e["body"],
                    "status": e["status"],
                    "version": e["version"],
                    "approved_override": bool(e["approved_override"]),
                    "eval": json.loads(e["eval_json"]) if e["eval_json"] else {},
                    "lead_score": e["lead_score"],
                    "lead_score_breakdown": json.loads(e["lead_score_breakdown_json"])
                    if e["lead_score_breakdown_json"]
                    else {},
                }
            )
    return {
        "companies": snapshot.get("companies", []),
        "contacts": snapshot.get("contacts", []),
        "research": snapshot.get("research", []),
        "emails": emails,
        "calendar_link": snapshot.get("calendar_link"),
        "timings": snapshot.get("timings"),
        "cost": run["cost"],
        "cost_breakdown": json.loads(run["cost_breakdown_json"]) if run["cost_breakdown_json"] else {},
        "run_id": run_id,
    }


def _require_updated(cur, run_id: int, email_id: str) -> None:
    if cur.rowcount == 0:
        raise LookupError(f"no email '{email_id}' in run {run_id} — nothing updated")


def recently_contacted_companies(
    cooldown_days: int, *, user_id: Optional[int] = None, db_path: Optional[str] = None
) -> List[str]:
    """Company names we generated outreach for within the last ``cooldown_days``.

    Counts a company as contacted if we generated ANY email for it, regardless of
    approval status: a rejected draft signals the company likely isn't a fit, so we
    back off for the cooldown too. Returns original names (newest first); the caller
    normalizes for matching. ``cooldown_days <= 0`` disables. Tolerant of a missing DB.

    Scope: pass ``user_id`` to restrict to that account's history (cooldown_scope
    "user"); pass ``None`` for global cross-account dedup (cooldown_scope "global").
    """
    if cooldown_days <= 0:
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=cooldown_days)).isoformat()
    where = "r.created_at >= ? AND e.company IS NOT NULL AND e.company != ''"
    params: List[Any] = [cutoff]
    if user_id is not None:
        where += " AND r.user_id = ?"
        params.append(user_id)
    try:
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT e.company, MAX(r.created_at) AS last_seen "
                "FROM emails e JOIN runs r ON e.run_id = r.id "
                f"WHERE {where} "
                "GROUP BY e.company ORDER BY last_seen DESC",
                params,
            ).fetchall()
        return [row["company"] for row in rows]
    except sqlite3.OperationalError:
        return []  # tables not created yet


def transition_email(
    run_id: int,
    email_id: str,
    action: str,
    *,
    user_id: int,
    expected_version: Optional[int] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Enforced approval transition — the ONE path callers should use.

    Loads the current status (scoped to ``user_id``'s run — another user's email is
    invisible, raising ``LookupError``), validates the ``action`` against the
    approval state machine (raises ``ValueError`` on an illegal transition), and
    records an override when approving a not-ready draft. Keeps the invariant at the
    data layer, so any caller (UI, API, script) gets the same guarantees rather than
    trusting a raw status setter.
    """
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT e.status, e.ready, e.version FROM emails e JOIN runs r ON e.run_id = r.id "
            "WHERE e.run_id = ? AND e.email_id = ? AND r.user_id = ?",
            (run_id, email_id, user_id),
        ).fetchone()
        if row is None:
            raise LookupError(f"no email '{email_id}' in run {run_id}")
        if expected_version is not None and row["version"] != expected_version:
            raise ConcurrencyConflict(
                f"email '{email_id}' changed since it was loaded — reload and retry"
            )
        new_status = apply_action(row["status"], action)  # raises on illegal transition
        # Approving a not-ready draft is a recorded override; other actions clear it.
        override = 1 if (action == "approve" and not row["ready"]) else 0
        # Conditional (optimistic) update: only apply if BOTH the status and the
        # version are still what we validated against. If a concurrent tab moved it,
        # rowcount is 0 → conflict, not a silent last-write-wins (High #7).
        cur = conn.execute(
            "UPDATE emails SET status = ?, approved_override = ?, version = version + 1 "
            "WHERE run_id = ? AND email_id = ? AND status = ? AND version = ?",
            (new_status, override, run_id, email_id, row["status"], row["version"]),
        )
        if cur.rowcount == 0:
            raise ConcurrencyConflict(
                f"email '{email_id}' changed since it was loaded — reload and retry"
            )
        new_version = row["version"] + 1
    return {"status": new_status, "approved_override": bool(override), "version": new_version}


def _set_email_status(
    run_id: int, email_id: str, status: str, approved_override: bool = False, db_path: Optional[str] = None
) -> None:
    """PRIVATE low-level setter — bypasses the approval state machine. Not part of
    the public API: callers must use :func:`transition_email` (enforced) or
    :func:`update_email_edit`. Kept for internal/test scaffolding only."""
    with _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE emails SET status = ?, approved_override = ? WHERE run_id = ? AND email_id = ?",
            (status, int(bool(approved_override)), run_id, email_id),
        )
        _require_updated(cur, run_id, email_id)


def update_email_edit(
    run_id: int,
    email_id: str,
    subject: str,
    body: str,
    eval_dict: Dict[str, Any],
    status: str,
    *,
    user_id: int,
    expected_version: Optional[int] = None,
    db_path: Optional[str] = None,
) -> int:
    """Persist a human edit. Returns the new version.

    When ``expected_version`` is supplied this is an optimistic write: if the row's
    version has moved (a concurrent edit/approval/rejection in another tab) the write
    is refused with ``ConcurrencyConflict`` rather than silently clobbering it. A
    missing/other-user email raises ``LookupError``."""
    faithful = (eval_dict.get("judge") or {}).get("faithful")
    with _connect(db_path) as conn:
        # Ownership gate + current version (scoped to the user's run).
        row = conn.execute(
            "SELECT e.version FROM emails e JOIN runs r ON e.run_id = r.id "
            "WHERE e.run_id = ? AND e.email_id = ? AND r.user_id = ?",
            (run_id, email_id, user_id),
        ).fetchone()
        if row is None:
            raise LookupError(f"no email '{email_id}' in run {run_id}")
        if expected_version is not None and row["version"] != expected_version:
            raise ConcurrencyConflict(
                f"email '{email_id}' changed since it was loaded — reload and retry"
            )
        # A fresh edit is a new version: any prior human override no longer applies
        # to this text, so clear approved_override to keep the audit trail honest.
        cur = conn.execute(
            "UPDATE emails SET subject = ?, body = ?, eval_json = ?, ready = ?, faithful = ?, "
            "status = ?, approved_override = 0, version = version + 1 "
            "WHERE run_id = ? AND email_id = ? AND version = ?",
            (
                subject,
                body,
                json.dumps(eval_dict),
                int(bool(eval_dict.get("ready"))),
                None if faithful is None else int(faithful),
                status,
                run_id,
                email_id,
                row["version"],
            ),
        )
        if cur.rowcount == 0:  # lost the row between our read and write
            raise ConcurrencyConflict(
                f"email '{email_id}' changed since it was loaded — reload and retry"
            )
        return row["version"] + 1
