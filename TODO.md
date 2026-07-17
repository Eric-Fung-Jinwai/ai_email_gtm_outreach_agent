# Technical TODO — GTM Outreach Agent

Productionizing a single-file Streamlit demo into a layered, **tested**,
**evaluation-driven** multi-agent GTM system.

> **Working title:** "Production-grade Multi-Agent GTM Outreach System"
> (not "productionizing" — the roadmap stops at logging/config/caching/eval/tests,
> no deploy/CI yet. Don't pick a title that dies to one follow-up question.)

## Guiding principle: depth beats breadth

Three things done well > seven done half-way. The **spine** below is the
committed scope; everything under "Trimmed / deferred" is polish that should
not eat time from the spine.

**Spine:** stateless agents + tests → RAG-grounded eval subsystem (eval + HITL +
persistence + cost, designed as ONE system) → concurrency (gated) → polish.

Legend: `[ ]` todo · `[~]` in progress · `[x]` done · ⚠️ blocker

---

## Phase 0 — Foundation & test harness

The meta-unlock. Stand up testing *first* so every later phase ships with tests.

- [ ] Create `backend/` package; move all non-UI logic out of the single file.
- [ ] Replace `ai_email_gtm_outreach_agent.py` with a thin `app.py` (frontend only).
- [ ] Delete dead code (`require_env`, commented-out `run_pipeline`).
- [ ] `backend/json_utils.py` — move `extract_json_or_raise`.
- [ ] Add `pytest`; create `tests/`.
- [ ] **Mocked-agent harness** — agents injected via factories so the pipeline
      runs with fake responses, **zero API calls**. This is the real unlock:
      it forces injectable agents and proves the architecture is decoupled.
- [ ] Tests: `extract_json_or_raise` (pure JSON, JSON-with-noise, garbage → raises).

**Acceptance:** `pytest` green; pipeline testable end-to-end with mocked agents.

---

## Phase 1 — Config via `.env` (no keys in UI)

- [ ] Add `pydantic-settings`; `backend/config.py` `Settings` loads
      `OPENAI_API_KEY`, `EXA_API_KEY`, `RAPIDAPI_KEY`, `REDIS_URL`, model IDs,
      TTLs, max workers.
- [ ] Load `.env` once at backend import; fail fast on missing keys.
- [ ] Fix `.env` formatting (no spaces around `=`).
- [ ] Commit `.env.example`; confirm `.env` stays gitignored.
- [ ] **Remove sidebar key inputs**; replace with a ✓/✗ status indicator.

**Acceptance:** Keys come only from `.env`; UI never accepts a key.

---

## Phase 2 — Frontend/backend separation + typed models

- [ ] `backend/models.py` — Pydantic: `Company`, `Contact`, `Insight`,
      `Email`, `EvalResult`, `RunRecord`, `PipelineResult`. All cross-layer
      data is typed.
- [ ] `backend/agents.py` — agent factories (no Streamlit import).
- [ ] `backend/tasks.py` — single-unit ops: `research_company`,
      `find_contacts_for_company`, `write_email`.
- [ ] `backend/pipeline.py` — `run_pipeline(...) -> PipelineResult` with a
      `progress_cb` callback so the UI gets stage updates without the backend
      importing Streamlit.
- [ ] `app.py` — inputs, calls `run_pipeline`, renders, manages session_state.
- [ ] Tests: Pydantic validation (good/bad payloads), pipeline wiring (mocked).

**Acceptance:** `grep -r "import streamlit" backend/` empty;
`grep -rE "agno|prompt|SqliteDb" app.py` empty.

---

## Phase 3 — Stateless agents  ⚠️ concurrency blocker, fix by construction

The cleanest fix for thread-safety isn't making shared memory safe — it's not
having shared memory. This is a fan-out of independent per-company tasks with
zero conversational continuity.

- [x] Drop `add_history_to_context`, `enable_user_memories`, `session_id`, `db`
      for the **contact finder + researcher** (the concurrent pair) — done so the
      asyncio.gather stage has no shared-SQLite contention.
- [x] Do the same for **company finder + email writer**. Removed the `SqliteDb`
      import and `_DB_FILE`; all four agents are now stateless.
- [x] Each agent = pure function of its input; no shared Agno memory / session DB.
- [x] Agno session SQLite is now fully unused and removed from the agents. The
      app DB (Phase 6) will be separate.
- [x] Tests: offline suite green with stateless agents; no cross-call leakage.

**Acceptance:** ✅ Agents carry no shared state (`grep` for
`SqliteDb|session_id|enable_user_memories|add_history_to_context` in agents.py is
empty). Phase 7 concurrency is unblocked.

---

## Phase 4 — RAG evidence store + Eval subsystem  ⭐ CENTERPIECE

The strongest part of the project. Built as a grounded generate→verify loop, not
a scorer bolted on after the fact.

### 4a — RAG evidence store (substrate for grounding)  ✅ DONE
- [x] `Insight` model (`backend/models.py`) carries `{text, source_url, source_type}`
      where source_type ∈ {website, reddit, **job_posting**}.
- [x] **Job-postings source (`backend/sources/jsearch.py`)** — deterministic
      (API call, not an agent); raw structured postings, no summarization layer.
  - [x] `httpx` + `tenacity` (retry 3x, exp backoff on `HTTPError`), 15s timeout,
        JSON parse. Both imported **lazily** so tests stay import-clean.
  - [x] **Graceful degradation:** no key / HTTP error / bad JSON → `[]`, never raises.
  - [x] Company match on **normalized `employer_name`** (strips Inc/LLC/Corp/etc.),
        `employer_website` ignored (often null), `job_publisher` never used. Rejects
        staffing reposts. Tested (IBM vs Jobot).
  - [x] Query `"jobs at <company>"`; `date_posted=month`, `num_pages=1`, `country`.
  - [x] Dedup by `job_id`; map kept postings → `Insight(source_type="job_posting", …)`.
  - [x] **JD condensation** — pulls tech-stack + location + remote + posted-at into a
        short evidence string (verified on the real IBM payload). Raw wall never used.
  - [x] `RAPIDAPI_KEY` from `.env` only; host header constant; `max_jobs` capped.
  - [ ] Cache job results by domain in Redis (Phase 8) — quota-capped free tier.
- [x] **Pipeline wiring:** `_research_for_company` augments LLM research with job
      evidence (best-effort, in a thread); job postings alone count as evidence and
      can rescue a company whose website/Reddit research failed. Tested.
- [ ] Email writer uses **only** retrieved evidence, tagging which evidence each line
      draws on (constrained generation) — belongs with 4c.
- [x] Embeddings/vector store **skipped** by design (small condensed corpora).
- [x] Tests: JSearch client mocked HTTP (match filter, dedup, max cap, empty,
      malformed JSON, exception→[], missing key→[]); pipeline augment+rescue test.

### 4b — Deterministic checks (free, fully testable — run FIRST as a gate)  ✅ DONE
- [x] `backend/evaluation/deterministic.py` + `EvalResult`/`CheckResult` models.
- [x] Checks: subject present, body present, word-count bound
      (`EMAIL_MIN_WORDS`/`EMAIL_MAX_WORDS`), CTA present, spam/punctuation list,
      calendar-link presence (only when a link was provided).
- [x] Wired into pipeline: every generated email carries `email["eval"]`; UI shows
      a ✅/⚠️ badge + failed-check details. Pure, offline, no model calls.
- [x] Tests: `tests/test_deterministic.py` (each check pass/fail, spam detail,
      conditional calendar check) + pipeline attaches eval.
- [ ] Auto-reject deterministic failures / route to human — Phase 5 (HITL).

### 4c — LLM-judge, grounded on the evidence store  ✅ DONE
- [x] `backend/evaluation/judge.py` + `JudgeVerdict`/`ClaimVerdict` models nested
      under `EvalResult.judge`.
- [x] **Faithfulness:** per-claim grounding against retrieved evidence only
      ("judge ONLY against evidence, never outside knowledge"); `faithful` inferred
      from claims when the model omits it.
- [x] **Coverage** + **personalization** coarse labels; **issues** list.
- [x] `temperature=0`; structured **per-claim** verdicts (`ClaimVerdict`).
- [x] **Cost gate:** runs only on emails that passed the deterministic gate; off by
      default (`ENABLE_LLM_JUDGE`), injectable `judge_agent`, `JUDGE_MODEL=gpt-4o`.
- [x] **Graceful:** judge errors set `error` (never masquerade as unfaithful).
- [x] Tests: parsing, faithful-inference, prompt grounding, graceful error, and
      pipeline gating (judged-only-when-passed, ungrounded-claim, evidence-grounded).

### 4d — Judge defensibility (this is what makes it interview-proof)  ✅ DONE (seed)
- [x] **Golden set** at `backend/evaluation/data/golden.json` — 12 hand-labeled,
      balanced faithful/unfaithful examples (funding/location/award/headcount
      hallucinations, generic-but-faithful, no-evidence cases). Seed to grow to 20–30.
- [x] **Agreement harness** (`backend/evaluation/golden.py`): runs the judge over
      the set, reports accuracy + hallucination-detection precision/recall
      (positive = unfaithful), counts judge errors. CLI: `python -m backend.evaluation.golden`.
- [x] Tests: golden set well-formed (both classes, unique ids, typed); metric
      correctness (confusion matrix, precision/recall) with a controllable mock
      judge; zero-recall detection; judge-error counting.
- [x] Ran against the REAL judge (gpt-4o, temp=0, 12 examples): **accuracy 75%,
      recall 100% (0 missed hallucinations, FN=0), precision 67% (3/6 faithful
      over-flagged)**. Conservative = correct direction for a review gate. Grow the
      set + inspect the 3 false positives to lift precision.

### 4e — Repair loop (bounded)  ✅ DONE
- [x] On faithfulness failure: **one** grounded rewrite (`_repair_email`) that lists
      the ungrounded claims and re-grounds strictly in evidence, then re-runs the
      deterministic gate + judge. Single retry — no cost spiral (`ENABLE_REPAIR`).
- [x] Adopt the rewrite only if now ready; else keep the original flagged for human.
- [x] UI shows repair status; tests cover success / failure-still-flagged / disabled.

**Acceptance:** ✅ Every generated email has an `EvalResult`; faithfulness judged
against retrieved evidence; judge agreement harness + golden set in place.
Remaining: run the harness against the real judge to record the live number.

---

## Phase 5 — Human-in-the-loop approval  ✅ DONE (statuses in session; durable in Phase 6)

Real workflow design — and eval *feeds* it.

- [x] State machine `backend/approval.py`: `drafted → {approved | rejected | edited}`;
      `edit` always allowed and loops to `edited`; decisions reversible; illegal
      transitions (repeat terminal action, unknown action/state) raise.
- [x] **Eval-routed queue:** UI splits ready (deterministic pass + faithful) vs
      "Needs review" (failed check / unfaithful / judge error). Not a separate step.
- [x] UI: per-email Approve / Reject / Edit; inline subject/body edit re-runs the
      deterministic gate and clears the stale judge verdict; status badge shown.
- [x] Pipeline stamps each email with a stable `id` + initial `drafted` status.
- [x] Tests: `tests/test_approval.py` (valid + illegal transitions); pipeline id/status.
- [ ] Persist statuses across refresh — Phase 6 (separate app DB).

> **Design decision — human has final authority.** The eval gate (deterministic +
> LLM judge) *routes attention*; it does not hard-block. A human may approve a
> draft the automated checks flagged — including one the LLM judge marked
> unfaithful — because the person, not the model, owns what gets sent. That
> override is **intentional**, but never silent: it requires an explicit
> "⚠️ Override & approve" action, is recorded on the email (`approved_override`),
> and the UI surfaces exactly why the automated checks failed so the human
> decides with full context. An edit that invalidates a prior judge verdict is
> likewise not auto-ready — it drops to review until re-judged or overridden.

**Acceptance:** No email reaches "final" without an explicit approve/edit;
statuses persist (Phase 6).

---

## Phase 6 — Persistence / campaign history  (separate app DB)  ✅ DONE

- [x] **Separate SQLite app DB** (`backend/persistence.py`, `tmp/campaigns.db` via
      `APP_DB_PATH`) — NOT the agno session DB. Schema: `runs` (immutable
      companies/contacts/research snapshot as JSON + inputs + nullable `cost` for
      Phase 9) and `emails` (normalized: status, ready, faithful, approved_override,
      eval_json). Emails are the only mutable rows → nothing diverges.
- [x] Persist each run: inputs, companies, contacts, evidence, emails, statuses.
- [x] ⚠️ **Single-writer:** persistence runs on the **main thread after** the async
      pipeline join (in `app.py`, post-`run_pipeline`). Workers never write.
- [x] UI: sidebar lists past runs (newest first) + reopen; approve/reject/edit
      persist durably (`update_email_status` / `update_email_edit`).
- [x] **Contact-cooldown suppression:** `recently_contacted_companies(cooldown_days)`
      + pipeline `suppress_companies` — a company we generated ANY email for (approved
      or rejected — a rejection means "not a fit", so back off too) is excluded from
      new runs for `CONTACT_COOLDOWN_DAYS` (default 30), then eligible again. Excluded
      in the finder prompt AND post-filtered (shared `text_utils.normalize_company_name`).
- [x] **Top-up toward N:** `_collect_companies` retries the finder (bounded, 3
      attempts), each excluding suppressed + already-found, aiming for N fresh
      companies. May return **fewer than N** if not enough eligible companies exist
      (stops early rather than looping/padding). Single call when nothing suppressed.
- [x] Tests: save/get round-trip, list newest-first, status/edit updates, override
      cleared on edit, update-raises-on-unknown, schema migration, cooldown
      within/outside window, pipeline suppression filter.

**Acceptance:** ✅ A run survives restart and is reloadable as a campaign; approval
statuses persist across refresh. (`tmp/` is gitignored → DB not committed.)

---

## Phase 7 — Concurrency  ✅ DONE (was gated on Phase 3)

Low-risk *once agents are stateless* — the danger was always the shared memory.

- [x] **Stage-level concurrency:** contacts + research first ran concurrently via
      `agent.arun` + `asyncio.gather`.
- [x] **Per-company fan-out (`_fanout_contacts_and_research`):** contacts + research
      fanned out per company via `agent.arun`, all under one `asyncio.gather`.
- [x] Bounded workers via `Settings.max_workers` (asyncio `Semaphore`).
- [x] Deterministic result ordering (gather preserves input company order).
- [x] Partial-failure isolation: a per-company exception → error record in that
      company's slot, batch continues.
- [x] Persistence writes happen on the main thread only (done in Phase 6).
- [x] `tenacity` retry/backoff on transient/rate-limit errors: `RetryingAgent`
      (`backend/retry.py`) wraps every agent + judge, retrying ONLY transient errors
      (429/408/409/5xx by status, or rate-limit/timeout/connection by class name),
      never deterministic ones (400/401/JSON). `API_MAX_RETRIES`/`API_RETRY_WAIT`.
- [x] Tests: ordering, partial-failure, worker-bound (semaphore serialization);
      transient-retry (predicate, backoff wrapper, pipeline end-to-end retry).
- [x] **Event-loop safe:** async-first `arun_pipeline()` + sync `run_pipeline()`
      wrapper, so the backend works under FastAPI/notebooks/async workers.
- [x] **Errored/empty records filtered** out of the email stage
      (`_usable_contacts_for_emails` / `_clean_research_for_emails`) — no missing
      evidence or raw exception text reaches the writer prompt.

### Review-driven safety hardening (applied)
- [x] Prompt-injection: research + email-writer agents told to treat retrieved
      web/Reddit/JSON as UNTRUSTED data, never as instructions.
- [x] Inferred emails excluded from drafts by default (`INCLUDE_INFERRED_CONTACTS`,
      default false) — deliverability/privacy/compliance. Full approval UX → Phase 5.
- [x] `debug_mode` is env-controlled (`DEBUG_AGENTS`, default false) so agent logs
      don't leak PII outside local dev.

**Acceptance:** Wall-clock for N=5 materially lower than sequential; single
failure yields partial result.

---

## Phase 8 — Redis caching  ⛔ DESCOPED (deliberate)

**Decision:** not building a Redis cache. Rationale:

- **Cooldown suppression (Phase 6) already removes the repeat-work path** — companies
  contacted in the last `CONTACT_COOLDOWN_DAYS` are excluded *before* research/contacts/
  JSearch, so there's little left for a cross-run cache to serve.
- **Single-user, free-tier:** no cross-user cache hits; within a run each company is
  fetched once already (no intra-run duplication).
- **Infra cost > payoff:** Redis means a server + docker-compose + connection +
  degradation handling + tests, for marginal gain. Declining it is the stronger call.
- If JSearch quota ever bites, a lightweight **SQLite/in-process TTL cache** (no new
  infra) covers the one real case — revisit only if needed.

**Revisit when → multi-user.** Cross-user caching (company researched for user A reused
for user B) becomes genuinely worth it there; that's a separate future effort, and the
`REDIS_URL` / `CACHE_TTL_*` config knobs are left in place as forward-looking hooks.

---

## Phase 11 — Multi-user  ✅ DONE (FastAPI + custom frontend)

The final piece. Replaced the single-user assumption with real accounts + per-user
data isolation, delivered as a **new FastAPI web app** (`server/`) with a custom
**HTML/CSS/JS** frontend (`frontend/`). The `backend/` package stayed pure logic;
the async-native `arun_pipeline` and the single persistence funnel made this an
additive layer, not a rewrite.

**Identity / auth (session-based, no external IdP):**
- [x] `users` table + stdlib `pbkdf2_hmac` (600k iters, per-user salt,
      `hmac.compare_digest`) — no third-party crypto dep. Register / login / logout /
      me over signed-cookie sessions (`SessionMiddleware`).
- [x] Hardened: `session.clear()` before establishing identity (anti-fixation);
      cookies `httponly` + `same_site=lax` + `https_only` in prod; default session
      secret **rejected** outside `ENVIRONMENT=development`; strict Origin/Referer
      guard (CSRF) on all cookie-auth mutations; username/password length+format
      limits; generic login errors (no user-exists oracle).

**Data user-scoping (all in `backend/persistence.py`):**
- [x] `user_id` added to `runs` (emails scope via the run join — single source of
      truth). `save_run`/`list_runs`/`get_run`/`transition_email`/`update_email_edit`/
      `recently_contacted_companies` take a **keyword-only** `user_id`; cross-tenant
      access returns `None` / raises → 404, never leaks another user's data.
- [x] **Migration backfill:** older single-user rows are re-owned by a deterministic
      passwordless `LOCAL_USER` (resolved by username, never a hardcoded id) so nothing
      is orphaned once queries require `user_id`. The legacy Streamlit `app.py` runs as
      that LOCAL_USER (still works, non-destructively).
- [x] SQLite hardened for the (now multi-request) FastAPI process: WAL +
      `busy_timeout` + `foreign_keys` per connection. Honest limit: writes are
      serialized *per process*, not a true global single-writer; Postgres is the swap
      for real concurrent traffic (contained by the persistence abstraction).

**Product decisions (resolved):**
- [x] **Cooldown scope:** configurable `COOLDOWN_SCOPE` (`user` | `global`), default
      `user`. Renamed from "org" — there is no org/team model, so cross-account dedup
      is genuinely *global*, documented as such (org scoping would need
      `organizations` + `memberships`).
- [x] **Keys/billing:** shared keys from `.env` (Phase-1 principle held), per-user
      cost attribution via `user_cost` (`GROUP BY`). Cost-abuse guard: in-memory
      per-user (2) + global (10) active-run caps → `429`.

**Run flow:** pipeline runs backgrounded as an asyncio task; progress streams over
**SSE** (`loop.call_soon_threadsafe` queue push — thread-safe defensively). Job
registry is in-memory / **single-process only** (documented; a multi-worker deploy
needs a shared broker). Opaque random job ids; finished jobs retained until consumed
or a TTL sweep.

- [x] Tests: `tests/test_api.py` (auth, 401, session switch, run→SSE→reopen,
      transition/edit, cross-user 404, CSRF 403, 429 cap, input validation, missing-key
      400) + `tests/test_email_ops.py` + user-scoping/backfill/cooldown/pragma tests in
      `tests/test_persistence.py`. Full suite green (160).

**Acceptance:** ✅ Multiple users register/log in; each sees only their own campaigns;
approvals/edits are tenant-isolated; cost is per-user; the eval subsystem is unchanged.
Run with `uvicorn server.main:app` (single worker); legacy Streamlit `app.py` still runs.

### Security hardening (review round) ✅ applied
- [x] **Secure-by-default:** `ENVIRONMENT` defaults to **production** — a forgotten
      setting fails closed (rejects the default session secret, HTTPS-only cookies).
      Local dev opts in with `ENVIRONMENT=development`.
- [x] **SSE lifecycle:** a client disconnect no longer drops a still-running job
      (stays counted toward the concurrency cap + reconnectable); finished-job TTL is
      measured from **completion**, not creation; SSE errors are generic to the client
      with the full exception logged server-side (correlation ref).
- [x] **Auth:** per-IP rate limit (429) on the PBKDF2-heavy login/register; dummy-hash
      on missing/passwordless users to flatten timing; registration race maps
      `IntegrityError` → 409; login stays a generic 401.
- [x] **Approval concurrency:** optimistic conditional update (`WHERE status=<read>`)
      → 409 on a lost race instead of silent last-write-wins.
- [x] **XSS/URL:** untrusted research/job URLs scrubbed to http(s) server-side (+ a
      client guard) before landing in an `href`.
- [x] **Headers/CSP:** strict `default-src 'self'` CSP, `frame-ancestors 'none'`,
      `nosniff`, `Referrer-Policy`, HSTS in prod. Self-contained same-origin frontend
      needs no external/inline resources, so the strict CSP is safe.
- [x] **CSRF:** Origin/Referer guard is now `TRUSTED_ORIGINS`-aware (correct behind a
      reverse proxy). A per-request CSRF token is the stronger next step (below).
- [x] **Prompt-injection:** the email-writer prompt builder now fences the
      contacts/research blocks in explicit BEGIN/END *untrusted-data* delimiters and
      repeats the data-not-instructions rule inline (not only in system instructions).
- [x] **Input:** whitespace-only `target_desc`/`offering_desc` rejected (422);
      `agents.py` agno imports are genuinely lazy again (import-clean restored).

### Security hardening (review round 2) ✅ applied
- [x] **Secure template:** `.env.example` no longer ships a working dev secret /
      `ENVIRONMENT=development`; production is the default and `require_web_config`
      now rejects the default **or any secret < 32 chars**, so a copied template
      can't silently run insecurely.
- [x] **Edit lost-update race:** added an `emails.version` optimistic-lock counter.
      `update_email_edit` + `transition_email` take an `expected_version`, bump it,
      and 409 on a stale write; the frontend threads the version and resyncs on 409.
      `expected_version` is **required** on the API mutation schemas (omitting it is
      422), so no client can silently fall back to last-write-wins — the optional
      persistence param remains only for the legacy Streamlit/internal path.
- [x] **Pipeline timeout:** each run has a hard deadline (`PIPELINE_TIMEOUT_SECONDS`);
      a hung provider call is cancelled and emits a terminal timeout event, freeing the
      per-user/global concurrency slot instead of holding it forever.
- [x] **SSE recovery:** the client now rides brief drops via EventSource
      auto-reconnect (backend keeps the running job alive), and after repeated failures
      falls back to the campaign list instead of silently giving up.
- [x] **Job sweep** runs on the stream + list endpoints too, so a finished-but-unopened
      job is reclaimed without waiting for the next POST.
- [x] **DB contention:** `sqlite3.OperationalError` ("database is locked") on approve/
      edit maps to a clean **503 retry**, not a 500.
- [x] **Rate-limiter memory:** keys are pruned/expired and hard-capped so spoofed
      client IPs can't grow the table unbounded (edge/proxy limiting still recommended).

### Deferred (real deployment, beyond this milestone — honest limits)
- [ ] **Postgres** for true concurrent multi-writer traffic (SQLite writes are
      serialized per process today; swap is contained behind `persistence.py`).
- [ ] **Durable/multi-process SSE** (shared broker, e.g. Redis pub/sub) — the job
      registry + auth rate limiter are in-memory / single-process; a multi-worker or
      horizontally-scaled deploy needs both at the edge/broker.
- [ ] **Per-request CSRF token** for cookie-auth writes (Origin check is the floor).
- [ ] **Encryption-at-rest + retention/deletion** — SQLite stores contact PII and
      generated emails in plaintext; a shared deploy needs FS ACLs, backups, and
      ideally at-rest encryption.
- [ ] **Organizations/memberships** model (would make cooldown genuinely org-scoped
      rather than the current global-across-accounts option).
- [ ] **OIDC/SSO** as an alternative to password auth.

**Discipline held:** ALL persistence stays in `backend/persistence.py` — no raw
`sqlite3` elsewhere — so user-scoping remained a contained, single-file change.

---

## Phase 9 — Cost / token tracking  ✅ DONE

Strong, underused agentic-work signal.

- [x] `backend/cost.py`: `MeteringAgent` wraps every agent (finder/contacts/research/
      writer/judge/repair) and records token usage into a `CostTracker`, grouped **by
      stage** (not model, so contact_finder and judge stay separate even though both
      default to gpt-4o); priced by a static `PRICING` table (estimates, per-1M tokens;
      unknown model → non-zero default).
- [x] `extract_usage` handles agno `metrics` + OpenAI-style `usage` (incl. token lists);
      returns (0,0) safely for fakes.
- [x] Pipeline returns `cost` + per-stage `cost_breakdown`; both persisted
      (`runs.cost` + `runs.cost_breakdown_json`) so reopened campaigns keep the detail;
      UI shows "💵 Estimated LLM cost this run: $X (per-stage …)".
- [x] Judge cost lands on its **own stage line** → makes the "gate the judge to bound
      cost" story concrete (deterministic gate keeps the paid gpt-4o judge off most drafts).
- [x] Tests: usage extraction (all shapes), pricing, tracker/breakdown, metering
      wrapper, pipeline captures cost end-to-end, cost persisted + listed.
- [ ] Attribute cache savings to a concrete "% cost cut" number — needs Phase 8 cache.

---

## Phase 10 — Observability & docs  ✅ DONE (observability); docs N/A

- [x] Structured `logging` (`backend/observability.py`): `configure_logging`
      (idempotent, `LOG_LEVEL`) called by the app; library modules use
      `logging.getLogger` and stay quiet until an entrypoint configures — so tests
      and imports emit no noise. `debug_mode` already env-gated (`DEBUG_AGENTS`).
- [x] **Per-stage timing:** pipeline records `timings` (companies, contacts_research,
      emails, evaluation, total); logged per stage + surfaced in the UI (⏱ caption).
- [x] Tests: logger factory, configure-idempotent; pipeline emits timings.
- [~] Docs / `README.md`: **intentionally skipped** — README was deleted on purpose.
      `.env.example` documents every setting; TODO.md is the living architecture doc.

---

## Security / integrity review (round 2)

- [x] **#3 Drafts bound to approved contacts.** After generation, each email's
      `(company, contact)` is matched against the emailable set we fed the writer;
      an unbound draft gets `eval.binding_error`, can never be ready
      (`email_is_ready`), skips the judge, and is routed to "Needs review" with the
      reason surfaced. Guards model slips / prompt injection.
- [x] **#4 Real contact validation.** `_usable_contacts_for_emails` hard-requires a
      syntactically valid email and explicit `inferred is False` (missing == inferred
      → excluded). Domain mismatch vs the company's canonical host is a SOFT flag
      (`domain_mismatch`) → lead-score −2 penalty, not a rejection; only compared when
      the website yields a host. Helpers in `text_utils`.
- [x] **#5 Malformed list elements can't crash the run.** Non-dict company and
      contact elements are coerced out (like emails already were) — no `.get` on a
      string/null.
- [x] **#6 Approval invariant at the data layer.** `persistence.transition_email`
      is the enforced command: loads status, validates via `apply_action` (raises on
      illegal), records an override when approving a not-ready draft. UI routes
      approve/reject through it; `update_email_status` demoted to internal primitive.
- [x] **#1 Auth / tenant isolation** — done in Phase 11 (multi-user): session auth +
      per-user data scoping enforced at the persistence layer (cross-tenant → 404).
- [ ] **#2 Retrieval-anchored grounding** — NEXT: app-controlled Exa/JSearch retrieval
      retained as `{evidence_id, url, excerpt, source_type}`; research model cites only
      those ids; writer/judge see only cited retrieved records. Full page verification
      stays pre-deploy.

## Trimmed / deferred (do NOT let these eat the spine)

- [x] **Lead scoring — deterministic sort (`backend/scoring.py`).** `seniority_score`
      (word-boundary title keywords, VP checked before C-level, no "cto"-in-"director"
      bug) + `lead_score` (seniority×2 + evidence[insight count capped + job-posting
      bonus] + readiness + verified-email). Pipeline attaches `lead_score` +
      breakdown to each email and sorts by priority; UI shows ⭐ score + breakdown.
      No LLM (personalization depth already lives in the Phase 4c judge). Industry
      match skipped — companies carry no structured category. Tested.
- [x] **Export formats — SKIPPED (not needed).** CSV/JSON/HubSpot export was
      optional polish; deliberately not building it. Data already lives durably in
      the app DB and is reopenable in-app, which covers the current need. Revisit if
      a real "hand off to CRM / share outside the app" workflow appears.

---

## Updated dependencies (`requirements.txt`)

```
agno>=2.2.10
streamlit>=1.33.0
pydantic>=2.7.0
pydantic-settings>=2.0.0
openai>=1.30.0
exa_py>=1.0.7
httpx>=0.27.0
redis>=5.0.0
tenacity>=8.2.0
pytest>=8.0.0
```

## Build order

0 → 1 → 2 → **3 (stateless, unblocks concurrency)** → **4 (eval centerpiece)** →
5 → 6 → 7 (gated on 3) → 8 → 9 → 10. Polish (lead-sort, exports) interleaved last.
Each phase leaves the app runnable and ships its own tests.
