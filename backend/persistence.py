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

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import get_settings


def _resolve_path(db_path: Optional[str]) -> str:
    return db_path or get_settings().app_db_path


@contextmanager
def _connect(db_path: Optional[str] = None):
    path = _resolve_path(db_path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
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
    },
    "emails": {
        "run_id": "INTEGER", "email_id": "TEXT", "company": "TEXT", "contact": "TEXT",
        "subject": "TEXT", "body": "TEXT", "status": "TEXT", "ready": "INTEGER",
        "faithful": "INTEGER", "approved_override": "INTEGER DEFAULT 0", "eval_json": "TEXT",
    },
}


def _migrate(conn) -> None:
    """Add any expected columns missing from an existing (older) schema."""
    for table, columns in _EXPECTED_COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init_db(db_path: Optional[str] = None) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
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
                cost_breakdown_json TEXT
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
                UNIQUE(run_id, email_id)
            );
            """
        )
        _migrate(conn)


def save_run(result: Dict[str, Any], inputs: Dict[str, Any], db_path: Optional[str] = None) -> int:
    """Persist a completed run and its emails. Returns the new run id."""
    init_db(db_path)
    snapshot = {
        "companies": result.get("companies", []),
        "contacts": result.get("contacts", []),
        "research": result.get("research", []),
        "calendar_link": result.get("calendar_link"),
    }
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO runs (created_at, target_desc, offering_desc, sender_name, "
            "sender_company, calendar_link, num_companies, email_style, snapshot_json, "
            "cost, cost_breakdown_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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
            ),
        )
        run_id = int(cur.lastrowid)
        for e in result.get("emails", []):
            ev = e.get("eval") or {}
            faithful = (ev.get("judge") or {}).get("faithful")
            conn.execute(
                "INSERT INTO emails (run_id, email_id, company, contact, subject, body, "
                "status, ready, faithful, approved_override, eval_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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
                ),
            )
    return run_id


def list_runs(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Summaries of all runs, newest first."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT r.id, r.created_at, r.target_desc, r.cost, COUNT(e.id) AS n_emails, "
            "SUM(CASE WHEN e.status='approved' THEN 1 ELSE 0 END) AS n_approved "
            "FROM runs r LEFT JOIN emails e ON e.run_id = r.id "
            "GROUP BY r.id ORDER BY r.id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_run(run_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Reconstruct a run into the same shape the pipeline returns (for reopening)."""
    with _connect(db_path) as conn:
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
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
                    "approved_override": bool(e["approved_override"]),
                    "eval": json.loads(e["eval_json"]) if e["eval_json"] else {},
                }
            )
    return {
        "companies": snapshot.get("companies", []),
        "contacts": snapshot.get("contacts", []),
        "research": snapshot.get("research", []),
        "emails": emails,
        "calendar_link": snapshot.get("calendar_link"),
        "cost": run["cost"],
        "cost_breakdown": json.loads(run["cost_breakdown_json"]) if run["cost_breakdown_json"] else {},
        "run_id": run_id,
    }


def _require_updated(cur, run_id: int, email_id: str) -> None:
    if cur.rowcount == 0:
        raise LookupError(f"no email '{email_id}' in run {run_id} — nothing updated")


def recently_contacted_companies(cooldown_days: int, db_path: Optional[str] = None) -> List[str]:
    """Company names we generated outreach for within the last ``cooldown_days``.

    Counts a company as contacted if we generated ANY email for it, regardless of
    approval status: a rejected draft signals the company likely isn't a fit, so we
    back off for the cooldown too. Returns original names (newest first); the caller
    normalizes for matching. ``cooldown_days <= 0`` disables. Tolerant of a missing DB.
    """
    if cooldown_days <= 0:
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=cooldown_days)).isoformat()
    try:
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT e.company, MAX(r.created_at) AS last_seen "
                "FROM emails e JOIN runs r ON e.run_id = r.id "
                "WHERE r.created_at >= ? AND e.company IS NOT NULL AND e.company != '' "
                "GROUP BY e.company ORDER BY last_seen DESC",
                (cutoff,),
            ).fetchall()
        return [row["company"] for row in rows]
    except sqlite3.OperationalError:
        return []  # tables not created yet


def update_email_status(
    run_id: int, email_id: str, status: str, approved_override: bool = False, db_path: Optional[str] = None
) -> None:
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
    db_path: Optional[str] = None,
) -> None:
    faithful = (eval_dict.get("judge") or {}).get("faithful")
    with _connect(db_path) as conn:
        # A fresh edit is a new version: any prior human override no longer applies
        # to this text, so clear approved_override to keep the audit trail honest.
        cur = conn.execute(
            "UPDATE emails SET subject = ?, body = ?, eval_json = ?, ready = ?, faithful = ?, "
            "status = ?, approved_override = 0 WHERE run_id = ? AND email_id = ?",
            (
                subject,
                body,
                json.dumps(eval_dict),
                int(bool(eval_dict.get("ready"))),
                None if faithful is None else int(faithful),
                status,
                run_id,
                email_id,
            ),
        )
        _require_updated(cur, run_id, email_id)
