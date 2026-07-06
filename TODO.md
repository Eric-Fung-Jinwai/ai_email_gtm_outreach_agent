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

### 4b — Deterministic checks (free, fully testable — run FIRST as a gate)
- [ ] Word-count bound, CTA present, subject present, banned/spam-phrase list,
      calendar-link presence when provided.
- [ ] Cheap → run before any LLM-judge call to bound cost.

### 4c — LLM-judge, grounded on the evidence store
- [ ] **Evidence completeness / faithfulness:** does every factual claim trace
      back to a retrieved insight? (RAG-faithfulness, NOT model general knowledge.)
- [ ] **Evidence coverage:** did the email actually use the strongest available
      evidence, or ignore it? (this absorbs "personalization depth".)
- [ ] **Personalization quality** + spam/tone.
- [ ] `temperature=0`, structured **per-claim** verdicts (not one vibe number).

### 4d — Judge defensibility (this is what makes it interview-proof)
- [ ] Small **golden set** (~20–30 hand-labeled emails: faithful/unfaithful,
      personalized/generic).
- [ ] Report **judge↔human agreement** on the golden set. "Faithfulness judge
      agrees with human labels 90%" >> "I added a faithfulness check".
- [ ] Tests: deterministic checks (exhaustive); judge harness runs on golden set.

### 4e — Repair loop (bounded)
- [ ] On faithfulness failure: regenerate with the offending claim stripped /
      tighter constraint. **One retry**, then flag for human. (Bounded → no cost spiral.)

**Acceptance:** Every generated email has an `EvalResult`; faithfulness is judged
against retrieved evidence; judge agreement reported on the golden set.

---

## Phase 5 — Human-in-the-loop approval

Real workflow design — and eval *feeds* it.

- [ ] State machine: `drafted → {approved | rejected | edited}`; `edited` loops
      back through approval. ("customized" is an action, not a terminal state.)
- [ ] **Eval prioritizes the human's attention:** deterministic-fail → auto-reject;
      borderline faithfulness → surface for review; clean → fast-path. HITL is not
      a separate manual step, it's an eval-routed queue.
- [ ] UI: per-email status controls, inline edit, re-submit edited drafts.
- [ ] Tests: state transitions (illegal transitions rejected).

**Acceptance:** No email reaches "final" without an explicit approve/edit;
statuses persist (Phase 6).

---

## Phase 6 — Persistence / campaign history  (separate app DB)

- [ ] **Separate SQLite app DB** — do NOT reuse the Agno session DB (that's agent
      memory and is tangled in the thread-safety story). Own schema:
      `runs`, `companies`, `contacts`, `emails` (with status + eval result + cost).
- [ ] Persist each run: target desc, companies, contacts, evidence, emails, statuses.
- [ ] ⚠️ **Single-writer:** workers RETURN results; persist once on the **main
      thread after the join** (Phase 7). Avoids "database is locked" and gives the
      deterministic-order aggregation for free.
- [ ] UI: list past runs, reopen a campaign, see statuses.
- [ ] Tests: write/read round-trip; status updates.

**Acceptance:** A run survives restart and is reloadable as a campaign.

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
