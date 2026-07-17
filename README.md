# Production-grade Multi-Agent GTM Outreach System

A multi-agent B2B outreach tool that finds target companies, identifies decision-maker
contacts, researches genuine insights (website + Reddit + job postings), and drafts
personalized emails — with a **grounded evaluation subsystem** (deterministic gate +
LLM faithfulness judge) that routes every draft to a human-in-the-loop approval queue.

It began as a single-file Streamlit demo and was productionized into a layered,
**tested**, **evaluation-driven**, **multi-user** system. The full engineering roadmap
and design decisions live in [`TODO.md`](./TODO.md).

> **Scope:** the roadmap intentionally stops at config / eval / persistence /
> concurrency / cost / observability / multi-user. Deployment concerns (Postgres,
> horizontal scaling, at-rest encryption, CI/CD) are documented as deferred, not built.

---

## Highlights

- **Four stateless agents** (company finder, contact finder, researcher, email writer)
  orchestrated as an async fan-out — no shared memory, safe to run concurrently.
- **RAG-grounded evaluation subsystem (the centerpiece):**
  - a free **deterministic gate** (subject/body/word-count/CTA/spam/calendar checks),
  - an **LLM faithfulness judge** that grounds every claim against retrieved evidence
    only, gated to run *after* the deterministic gate to bound cost,
  - a **golden set + agreement harness** so the judge itself is measured, and
  - a **bounded repair loop** (one grounded rewrite on a faithfulness failure).
- **Human-in-the-loop approval** with an enforced state machine
  (`drafted → approved | rejected | edited`), eval-routed into "Ready" vs "Needs review".
- **Multi-user web app** (FastAPI + a dependency-free HTML/CSS/JS frontend) with session
  auth, per-user data isolation, per-user cost attribution, and live SSE progress.
- **Deterministic lead scoring**, **cost/token metering per stage**, **per-stage timing**,
  **cooldown suppression** (don't re-contact a company within N days), and structured logging.
- **~175 offline tests** — the whole pipeline runs with fake agents, zero API calls.

---

## Architecture

```
backend/            Pure domain logic (no web framework imported)
  agents.py         Agent factories + injectable Agents container (agno, lazy import)
  pipeline.py       Async 4-stage orchestration: arun_pipeline() / run_pipeline()
  models.py         Pydantic models (Company, Contact, Insight, Email, EvalResult, …)
  evaluation/       Deterministic gate, LLM judge, gating, golden-set harness
  sources/jsearch.py  Job-postings evidence (deterministic API call, graceful degradation)
  persistence.py    Separate SQLite app DB — users, runs, emails (user-scoped)
  email_ops.py      Shared edit re-evaluation + failure-reason helpers
  scoring.py cost.py retry.py observability.py text_utils.py config.py

server/             FastAPI web layer (the multi-user frontend/backend seam)
  main.py           App wiring: session middleware, security headers, routers, static mount
  auth.py           register / login / logout / me (session cookies)
  runs.py           Start run (backgrounded) + SSE progress + campaigns + approve/edit
  deps.py           current_user, overridable agents-factory & db-path, CSRF/origin guard
  ratelimit.py      In-memory per-IP auth rate limiter
  schemas.py        Request/response validation

frontend/           Static SPA (no build step, no CDN): index.html, app.js, styles.css
app.py              Legacy single-user Streamlit frontend (still runnable)
tests/              Offline pytest suite (fake agents, temp DBs)
```

The backend is deliberately framework-agnostic — `grep -rn "import streamlit\|import
fastapi" backend/` is empty. `arun_pipeline()` is async-native, so the same logic drives
Streamlit, FastAPI, notebooks, or async workers.

---

## Setup

Requires **Python 3.12+**.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env` — only two keys are required:

```ini
OPENAI_API_KEY=sk-...
EXA_API_KEY=...

# Optional: job-postings evidence (JSearch via RapidAPI)
RAPIDAPI_KEY=

# Web app: secure-by-default — with ENVIRONMENT unset it is treated as PRODUCTION,
# which REQUIRES a strong SESSION_SECRET (>=32 chars) and HTTPS-only cookies.
# For local development, opt in explicitly:
ENVIRONMENT=development
SESSION_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
```

Keys are read only from the environment / `.env` — never entered in the UI. The app DB
lives at `tmp/campaigns.db` (`APP_DB_PATH`, gitignored). Every setting is documented in
[`.env.example`](./.env.example).

---

## Running

### Multi-user web app (FastAPI + custom frontend)

```bash
uvicorn server.main:app --reload   # single worker (see note below)
```

Open http://127.0.0.1:8000, register an account, and start a run. Each user sees only
their own campaigns; progress streams live over SSE; approvals/edits persist.

> **Single-process:** the SSE job registry and auth rate limiter are in-memory, so run a
> **single** uvicorn worker. Multi-worker/horizontal scaling needs a shared broker
> (documented as deferred in `TODO.md`).

### Legacy single-user Streamlit UI

```bash
streamlit run app.py
```

Still fully functional; it owns its data as a deterministic local user.

---

## Testing & evaluation

```bash
pytest -q                          # full offline suite (~175 tests, no API calls)
python -m backend.evaluation.golden  # run the LLM judge against the golden set (needs OPENAI_API_KEY)
```

The judge's recorded seed result on 12 hand-labeled examples (gpt-4o, temp=0):
**accuracy 75%, hallucination recall 100% (0 missed), precision 67%** — conservative,
the correct direction for a review gate.

---

## Security posture

Built for authenticated multi-user use, hardened over two review rounds:

- **Secure-by-default config:** production is the default; the default/weak session
  secret is rejected; cookies are `HttpOnly` + `SameSite=Lax` + `Secure` in production.
- **Auth:** PBKDF2 (600k iterations, per-user salt, constant-time compare), anti
  session-fixation, generic login errors, dummy-hash on unknown users, per-IP rate limit.
- **Tenant isolation:** all persistence is `user_id`-scoped; cross-tenant access → 404.
- **CSRF:** Origin/Referer guard (trusted-origins aware) on cookie-auth mutations.
- **Optimistic locking:** an `emails.version` counter (required on API mutations) → 409
  on a stale write, no silent last-write-wins.
- **Abuse/robustness:** per-user + global concurrent-run caps (429), a hard pipeline
  timeout that frees the slot, generic client-facing errors with server-side logging,
  and strict CSP / clickjacking / nosniff headers.
- **Prompt-injection:** retrieved web/Reddit/JSON is fenced as untrusted data with the
  "data, not instructions" rule repeated in the prompt builder; drafts are bound to the
  verified-contact set; inferred emails are excluded by default.
- **URLs:** untrusted research/job URLs are scrubbed to http(s) before rendering.

Deferred to a real deployment (see `TODO.md`): Postgres for concurrent multi-writer
traffic, durable multi-process SSE + edge rate limiting, a per-request CSRF token,
encryption-at-rest + retention, an organizations/memberships model, and OIDC/SSO.

---

## Tech stack

Python 3.12 · [agno](https://github.com/agno-agi/agno) agents on OpenAI models
(gpt-5.4-nano / gpt-4o) · Exa + JSearch retrieval · Pydantic / pydantic-settings ·
FastAPI + Starlette sessions · SQLite (WAL) · Streamlit (legacy UI) · pytest.
