"""Campaign endpoints: start a run (backgrounded), stream progress (SSE), list /
reopen campaigns, and drive per-email approval + edits — all scoped to the
authenticated user.

Job registry note (point 4): jobs live in an in-memory dict. This is correct for
a single-process deployment (``uvicorn`` with one worker). Across multiple workers
or a restart it does NOT hold — an SSE request could land on a process that does
not own the job. A durable multi-process design would need a shared broker
(Redis pub/sub, a DB-backed queue); intentionally out of scope here.
"""

import asyncio
import json
import logging
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from backend import persistence
from backend.config import get_settings
from backend.email_ops import failure_reasons, reevaluate_edited_email
from backend.evaluation.gating import email_is_ready
from backend.pipeline import arun_pipeline
from backend.text_utils import safe_http_url
from server.deps import (
    AgentsFactory,
    DbPath,
    OriginGuard,
    current_user,
    get_agents_factory,
    get_db_path,
)
from server.schemas import EditRequest, RunRequest, RunStarted, TransitionRequest

router = APIRouter(prefix="/api", tags=["runs"])
logger = logging.getLogger("gtm.server")

# --- In-memory job registry (single-process; see module docstring) ----------

_JOB_TTL_SECONDS = 300          # retain a finished, unconsumed job this long
_MAX_ACTIVE_PER_USER = 2        # cap concurrent paid pipelines per account (point 8)
_MAX_ACTIVE_GLOBAL = 10         # global safety cap (shared keys/billing)


@dataclass
class _Job:
    job_id: str
    user_id: int
    queue: "asyncio.Queue[Dict[str, Any]]"
    loop: asyncio.AbstractEventLoop
    status: str = "running"           # running | done | error
    run_id: Optional[int] = None
    created_at: float = field(default_factory=time.monotonic)
    finished_at: Optional[float] = None  # set when status leaves "running"


_JOBS: Dict[str, _Job] = {}


def _sweep() -> None:
    """Drop FINISHED jobs whose client never consumed the terminal event, past
    their TTL. TTL is measured from completion (``finished_at``), not creation, so
    a long run can't be swept the instant it finishes and before the browser opens
    its stream (Critical #3)."""
    now = time.monotonic()
    stale = [
        j for j, job in _JOBS.items()
        if job.finished_at is not None and now - job.finished_at > _JOB_TTL_SECONDS
    ]
    for jid in stale:
        _JOBS.pop(jid, None)


def _active_counts(user_id: int) -> tuple[int, int]:
    running = [j for j in _JOBS.values() if j.status == "running"]
    return sum(1 for j in running if j.user_id == user_id), len(running)


def _emit_threadsafe(job: _Job, event: Dict[str, Any]) -> None:
    # progress_cb runs on the event-loop thread today, but push defensively so a
    # future threaded emitter can't corrupt the queue (point 5).
    job.loop.call_soon_threadsafe(job.queue.put_nowait, event)


async def _run_pipeline_job(
    job: _Job,
    req: RunRequest,
    agents,
    suppress: List[str],
    db_path: Optional[str],
) -> None:
    def progress_cb(stage: int, total: int, message: str, detail: str = "") -> None:
        _emit_threadsafe(
            job, {"type": "progress", "stage": stage, "total": total, "message": message, "detail": detail}
        )

    try:
        # Hard deadline: a hung provider call is cancelled so it can't hold a
        # concurrency slot forever (wait_for cancels the coroutine on timeout).
        timeout = get_settings().pipeline_timeout_seconds
        result = await asyncio.wait_for(
            arun_pipeline(
                target_desc=req.target_desc.strip(),
                offering_desc=req.offering_desc.strip(),
                sender_name=req.sender_name.strip(),
                sender_company=req.sender_company.strip(),
                calendar_link=(req.calendar_link or "").strip() or None,
                num_companies=req.num_companies,
                email_style=req.email_style,
                agents=agents,
                progress_cb=progress_cb,
                suppress_companies=suppress,
            ),
            timeout=timeout,
        )
        # Persist on the loop thread right after the join (single-writer per task);
        # keep the calendar link so later edits re-validate against it (point 3).
        result["calendar_link"] = (req.calendar_link or "").strip() or None
        inputs = {
            "target_desc": req.target_desc.strip(),
            "offering_desc": req.offering_desc.strip(),
            "sender_name": req.sender_name.strip(),
            "sender_company": req.sender_company.strip(),
            "num_companies": req.num_companies,
            "email_style": req.email_style,
        }
        run_id = persistence.save_run(result, inputs, user_id=job.user_id, db_path=db_path)
        job.run_id = run_id
        job.status = "done"
        job.finished_at = time.monotonic()
        _emit_threadsafe(job, {"type": "done", "run_id": run_id})
    except asyncio.TimeoutError:
        job.status = "error"
        job.finished_at = time.monotonic()
        logger.warning("pipeline job %s for user %s timed out", job.job_id, job.user_id)
        _emit_threadsafe(job, {
            "type": "error",
            "message": f"The outreach run timed out (ref {job.job_id[:8]}). Please try again.",
        })
    except Exception:  # surface a clean terminal event; never crash the loop
        job.status = "error"
        job.finished_at = time.monotonic()
        # Log the full error server-side (may contain provider internals / paths);
        # send the browser only a generic, correlatable message (High #8).
        logger.exception("pipeline job %s failed for user %s", job.job_id, job.user_id)
        _emit_threadsafe(job, {
            "type": "error",
            "message": f"The outreach run failed (ref {job.job_id[:8]}). Please try again.",
        })


def _decorate_run(run: Dict[str, Any]) -> Dict[str, Any]:
    """Prepare a run for the client: attach computed ``ready`` + ``failure_reasons``
    (so the frontend doesn't reimplement gating), and scrub untrusted research URLs
    down to plain http(s) so they can't inject a dangerous ``href`` (High #4)."""
    for e in run.get("emails", []):
        ev = e.get("eval") or {}
        e["ready"] = email_is_ready(ev)
        e["failure_reasons"] = failure_reasons(e)
    for r in run.get("research", []):
        for insight in r.get("insights", []):
            if isinstance(insight, dict) and "source_url" in insight:
                insight["source_url"] = safe_http_url(insight.get("source_url"))
    return run


# --- Routes -----------------------------------------------------------------

@router.post("/runs", response_model=RunStarted, dependencies=[OriginGuard])
async def start_run(
    req: RunRequest,
    request: Request,
    user_id: int = Depends(current_user),
    db_path=DbPath,
    agents_factory: AgentsFactory = Depends(get_agents_factory),
) -> RunStarted:
    _sweep()
    per_user, total = _active_counts(user_id)
    if per_user >= _MAX_ACTIVE_PER_USER or total >= _MAX_ACTIVE_GLOBAL:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many concurrent runs — wait for an in-flight run to finish",
        )

    # Building agents validates keys (real factory raises on missing keys) without
    # any network I/O. Test factories return fakes and never raise.
    try:
        agents = agents_factory(req.email_style)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    settings = get_settings()
    suppress: List[str] = []
    if settings.enable_contact_suppression:
        scope_user = user_id if settings.cooldown_scope == "user" else None
        try:
            suppress = persistence.recently_contacted_companies(
                settings.contact_cooldown_days, user_id=scope_user, db_path=db_path
            )
        except Exception:
            suppress = []

    job = _Job(
        job_id=secrets.token_urlsafe(16),  # opaque, unguessable job id (point 4)
        user_id=user_id,
        queue=asyncio.Queue(),
        loop=asyncio.get_running_loop(),
    )
    _JOBS[job.job_id] = job
    asyncio.create_task(_run_pipeline_job(job, req, agents, suppress, db_path))
    return RunStarted(job_id=job.job_id)


@router.get("/runs/stream/{job_id}")
async def stream_run(job_id: str, user_id: int = Depends(current_user)) -> StreamingResponse:
    _sweep()  # reclaim finished-but-unconsumed jobs even if no new run has started
    job = _JOBS.get(job_id)
    if job is None or job.user_id != user_id:  # unknown OR not the owner
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")

    async def event_stream():
        # Only remove the job once its TERMINAL event has actually been delivered.
        # A client disconnect (GeneratorExit) must NOT drop a still-running job:
        # the pipeline task keeps consuming quota, so it must stay counted toward the
        # concurrency cap and remain reconnectable; the TTL sweep reclaims it once it
        # finishes (Critical #2).
        while True:
            event = await job.queue.get()
            yield f"data: {json.dumps(event)}\n\n"
            if event["type"] in ("done", "error"):
                _JOBS.pop(job_id, None)  # terminal consumed → free it
                return

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)


@router.get("/runs")
def list_campaigns(user_id: int = Depends(current_user), db_path=DbPath) -> Dict[str, Any]:
    _sweep()  # opportunistic cleanup on a frequently-hit endpoint
    return {
        "runs": persistence.list_runs(user_id=user_id, db_path=db_path),
        "total_cost": persistence.user_cost(user_id=user_id, db_path=db_path),
    }


@router.get("/runs/{run_id}")
def get_campaign(run_id: int, user_id: int = Depends(current_user), db_path=DbPath) -> Dict[str, Any]:
    run = persistence.get_run(run_id, user_id=user_id, db_path=db_path)
    if run is None:  # missing OR another user's run → indistinguishable 404
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return _decorate_run(run)


@router.post("/runs/{run_id}/emails/{email_id}/transition", dependencies=[OriginGuard])
def transition(
    run_id: int,
    email_id: str,
    body: TransitionRequest,
    user_id: int = Depends(current_user),
    db_path=DbPath,
) -> Dict[str, Any]:
    try:
        return persistence.transition_email(
            run_id, email_id, body.action, user_id=user_id,
            expected_version=body.expected_version, db_path=db_path,
        )
    except persistence.ConcurrencyConflict as exc:  # lost an optimistic-update race
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:  # illegal state-machine transition
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except LookupError:  # unknown/other-user email
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="email not found")
    except sqlite3.OperationalError:  # e.g. "database is locked" under contention
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="storage busy — please retry"
        )


@router.post("/runs/{run_id}/emails/{email_id}/edit", dependencies=[OriginGuard])
def edit(
    run_id: int,
    email_id: str,
    body: EditRequest,
    user_id: int = Depends(current_user),
    db_path=DbPath,
) -> Dict[str, Any]:
    run = persistence.get_run(run_id, user_id=user_id, db_path=db_path)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    email = next((e for e in run.get("emails", []) if e.get("id") == email_id), None)
    if email is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="email not found")

    ev = reevaluate_edited_email(email, body.subject, body.body, run.get("calendar_link"))
    try:
        new_version = persistence.update_email_edit(
            run_id, email_id, body.subject, body.body, ev, "edited",
            user_id=user_id, expected_version=body.expected_version, db_path=db_path,
        )
    except persistence.ConcurrencyConflict as exc:  # concurrent edit/approval clobber
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="email not found")
    except sqlite3.OperationalError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="storage busy — please retry"
        )
    return {
        "id": email_id,
        "subject": body.subject,
        "body": body.body,
        "status": "edited",
        "version": new_version,
        "eval": ev,
        "ready": email_is_ready(ev),
        "failure_reasons": failure_reasons({"eval": ev}),
    }
