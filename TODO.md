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
- [x] Tests: `tests/test_persistence.py` — save/get round-trip (evidence preserved),
      list newest-first, status update, edit update, missing run, schema auto-create.

**Acceptance:** ✅ A run survives restart and is reloadable as a campaign; approval
statuses persist across refresh. (`tmp/` is gitignored → DB not committed.)

---

## Phase 7 — Concurrency  ⚠️ GATED on Phase 3

Low-risk *once agents are stateless* — the danger was always the shared memory.

- [x] **Stage-level concurrency:** contacts + research first ran concurrently via
      `agent.arun` + `asyncio.gather`.
- [x] **Per-company fan-out (`_fanout_contacts_and_research`):** contacts + research
      fanned out per company via `agent.arun`, all under one `asyncio.gather`.
- [x] Bounded workers via `Settings.max_workers` (asyncio `Semaphore`).
- [x] Deterministic result ordering (gather preserves input company order).
- [x] Partial-failure isolation: a per-company exception → error record in that
      company's slot, batch continues.
- [ ] Persistence writes happen on the main thread only (see Phase 6).
- [ ] `tenacity` retry/backoff on transient/rate-limit errors.
- [x] Tests: ordering, partial-failure, worker-bound (semaphore serialization).
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

## Phase 8 — Redis caching

- [ ] Add `redis`; `REDIS_URL` in config; `docker-compose.yml` (`redis:7`).
- [ ] `backend/cache.py`: `normalize_domain(website)`, `get_json/set_json(ttl)`,
      **graceful no-op on `ConnectionError`** (never crash the pipeline).
- [ ] Cache-aside keyed by **domain**: `gtm:research:{domain}` (~7d),
      `gtm:contacts:{domain}` (~3d). Surface hit/miss via `progress_cb`.
- [ ] Report cache cost payoff (ties to Phase 9): "cache cut per-run cost by X%".
- [ ] Tests: key normalization (exhaustive), hit/miss, no-op when Redis down.

**Acceptance:** Overlapping company set skips Exa+LLM for cached domains.

---

## Phase 9 — Cost / token tracking

Cheap once logging exists; strong, underused agentic-work signal.

- [ ] Capture per-call token usage from run output; map to a pricing table.
- [ ] Store per-run cost in the app DB; show per-run cost in UI.
- [ ] Note the irony: the LLM-judge ADDS cost → that's exactly why deterministic
      checks gate it (Phase 4b). Frame as a unit-economics story.
- [ ] Attribute cache savings (Phase 8) to a concrete "% cost cut" number.

---

## Phase 10 — Observability & docs

- [ ] Replace `print`/`debug_mode` with `logging`; per-stage timing.
- [ ] Update `README.md`: structure, `.env` setup, Redis/docker, eval/golden-set,
      run command (`streamlit run app.py`). Document architecture.

---

## Trimmed / deferred (do NOT let these eat the spine)

- [ ] **Lead scoring — trimmed to deterministic sort only.** Seniority from title
      keywords + industry match from category. Do NOT rebuild the LLM judge here;
      "personalization depth" already lives in eval coverage (Phase 4c).
- [ ] **Export formats — last, cheap.** CSV/JSON + "HubSpot-style CSV". Nearly
      free once typed models + persistence exist. Product-sense polish, not a headline.

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
