"use strict";

// --- tiny helpers -----------------------------------------------------------
const $ = (sel) => document.querySelector(sel);
const el = (id) => document.getElementById(id);
const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Defense-in-depth: only allow http(s) links in href (server already scrubs).
const safeUrl = (u) => (/^https?:\/\//i.test(String(u || "")) ? u : null);

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts); // same-origin → cookies sent automatically
  let data = null;
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) {
    const detail = (data && (data.detail || data.message)) || res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

let CONFIG = null;
let CURRENT_RUN_ID = null;

// --- auth -------------------------------------------------------------------
function showAuth() { el("auth-view").classList.remove("hidden"); el("app-view").classList.add("hidden"); }
function showApp() { el("auth-view").classList.add("hidden"); el("app-view").classList.remove("hidden"); }

el("auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const action = e.submitter?.dataset.action || "login";
  const username = el("auth-username").value.trim();
  const password = el("auth-password").value;
  el("auth-error").textContent = "";
  try {
    await api("POST", `/api/auth/${action}`, { username, password });
    await boot();
  } catch (err) {
    el("auth-error").textContent = err.message;
  }
});

el("logout").addEventListener("click", async () => {
  try { await api("POST", "/api/auth/logout"); } catch (_) {}
  showAuth();
});

// --- config + campaigns -----------------------------------------------------
async function loadConfig() {
  CONFIG = await api("GET", "/api/config");
  const styles = CONFIG.email_styles || ["Professional"];
  el("email_style").innerHTML = styles.map((s) => `<option>${esc(s)}</option>`).join("");
  el("num_companies").max = CONFIG.max_companies || 10;
  el("keystatus").innerHTML =
    `<span>OpenAI: ${CONFIG.has_openai ? "✅" : "❌"}</span>` +
    `<span>Exa: ${CONFIG.has_exa ? "✅" : "❌"}</span>`;
}

async function loadCampaigns() {
  const { runs, total_cost } = await api("GET", "/api/runs");
  el("usercost").textContent = `Your total estimated LLM spend: $${(total_cost || 0).toFixed(4)}`;
  el("campaigns").innerHTML = runs.length
    ? runs.map((r) =>
        `<button class="campaign card ${r.id === CURRENT_RUN_ID ? "active" : ""}" data-id="${r.id}">
           #${r.id} · ${esc((r.target_desc || "").slice(0, 26))} · ${r.n_emails} emails</button>`).join("")
    : `<span class="muted small">No saved campaigns yet.</span>`;
  document.querySelectorAll(".campaign").forEach((b) =>
    b.addEventListener("click", () => openCampaign(Number(b.dataset.id))));
}

async function openCampaign(runId) {
  const run = await api("GET", `/api/runs/${runId}`);
  CURRENT_RUN_ID = runId;
  renderResults(run);
  loadCampaigns();
}

// --- run --------------------------------------------------------------------
el("run-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  el("run-error").textContent = "";
  const payload = {
    target_desc: el("target_desc").value.trim(),
    offering_desc: el("offering_desc").value.trim(),
    sender_name: el("sender_name").value.trim(),
    sender_company: el("sender_company").value.trim(),
    calendar_link: el("calendar_link").value.trim() || null,
    num_companies: Number(el("num_companies").value),
    email_style: el("email_style").value,
  };
  const btn = el("run-btn");
  btn.disabled = true;
  el("results").innerHTML = "";
  try {
    const { job_id } = await api("POST", "/api/runs", payload);
    streamProgress(job_id);
  } catch (err) {
    el("run-error").textContent = err.message;
    btn.disabled = false;
  }
});

function streamProgress(jobId) {
  const wrap = el("progress-wrap");
  wrap.classList.remove("hidden");
  el("progress-bar").style.width = "0%";
  el("progress-msg").textContent = "Starting…";
  const es = new EventSource(`/api/runs/stream/${jobId}`);
  let terminal = false; // saw a done/error event → stop reconnecting
  let errors = 0;

  es.onmessage = async (ev) => {
    const data = JSON.parse(ev.data);
    if (data.type === "progress") {
      const pct = data.total ? Math.round((data.stage / data.total) * 100) : 0;
      el("progress-bar").style.width = pct + "%";
      el("progress-msg").textContent = `${data.stage}/${data.total} ${data.message} ${data.detail || ""}`;
    } else if (data.type === "done") {
      terminal = true; es.close();
      el("progress-bar").style.width = "100%";
      el("progress-msg").textContent = "Completed";
      el("run-btn").disabled = false;
      await openCampaign(data.run_id);
      setTimeout(() => wrap.classList.add("hidden"), 1200);
    } else if (data.type === "error") {
      terminal = true; es.close();
      el("run-error").textContent = data.message || "Pipeline failed";
      el("run-btn").disabled = false;
      wrap.classList.add("hidden");
    }
  };

  // The backend keeps a disconnected-but-running job alive, so let EventSource
  // auto-reconnect through brief network/proxy blips. Only give up after several
  // failures — then fall back to the campaign list (the run may have finished and
  // been persisted) instead of leaving the user without feedback.
  es.onerror = () => {
    if (terminal) { es.close(); return; }
    errors += 1;
    el("progress-msg").textContent = "Reconnecting…";
    if (errors >= 6) {
      es.close();
      el("run-btn").disabled = false;
      wrap.classList.add("hidden");
      el("run-error").textContent =
        "Lost the live connection. If the run finished it will appear under Past campaigns.";
      loadCampaigns();
    }
  };
}

// --- rendering --------------------------------------------------------------
function renderResults(run) {
  const parts = [];
  if (run.cost != null) {
    const bd = run.cost_breakdown || {};
    const perStage = Object.entries(bd).map(([k, v]) => `${k}: $${(v.cost || 0).toFixed(4)}`).join(", ");
    parts.push(`<p class="muted small">💵 Estimated LLM cost: $${run.cost.toFixed(4)}${perStage ? " (" + esc(perStage) + ")" : ""}</p>`);
  }
  if (run.timings) {
    const per = Object.entries(run.timings).filter(([k]) => k !== "total")
      .map(([k, v]) => `${k}: ${v.toFixed(1)}s`).join(", ");
    parts.push(`<p class="muted small">⏱ Total: ${(run.timings.total || 0).toFixed(1)}s (${esc(per)})</p>`);
  }

  parts.push(`<h2 class="section-title">Target companies</h2>`);
  parts.push((run.companies || []).map((c) =>
    `<div class="card"><strong>${esc(c.name)}</strong><div class="muted small">${esc(c.website || "")}</div><div>${esc(c.why_fit || "")}</div></div>`
  ).join("") || `<p class="muted">No companies found.</p>`);

  parts.push(`<h2 class="section-title">Research insights</h2>`);
  parts.push((run.research || []).map((r) => {
    const items = (r.insights || []).slice(0, 6).map((i) => {
      if (i && typeof i === "object") {
        const url = safeUrl(i.source_url);
        const src = url ? ` <a href="${esc(url)}" target="_blank" rel="noopener noreferrer">[${esc(i.source_type || "src")}]</a>` : (i.source_type ? ` <span class="muted">(${esc(i.source_type)})</span>` : "");
        return `<li>${esc(i.text)}${src}</li>`;
      }
      return `<li>${esc(i)}</li>`;
    }).join("");
    return `<div class="card"><strong>${esc(r.name)}</strong><ul>${items}</ul></div>`;
  }).join("") || `<p class="muted">No research insights.</p>`);

  // Emails split ready / needs-review
  const emails = run.emails || [];
  const ready = emails.filter((e) => e.ready);
  const review = emails.filter((e) => !e.ready);
  parts.push(`<h2 class="section-title">Outreach emails</h2>`);
  parts.push(ready.map((e, i) => emailCard(e, i + 1)).join("") || `<p class="muted">No drafts ready to send.</p>`);
  if (review.length) {
    parts.push(`<h3 class="needs-review-title">⚠️ Needs review (${review.length})</h3>`);
    parts.push(review.map((e, i) => emailCard(e, i + 1)).join(""));
  }

  el("results").innerHTML = parts.join("");
  emails.forEach((e) => wireEmailControls(e));
}

const STATUS_BADGE = { drafted: "📝", approved: "✅", rejected: "🚫", edited: "✏️" };

function emailCard(e, i) {
  const ev = e.eval || {};
  const badge = ev.passed ? "✅" : "⚠️";
  const score = e.lead_score != null ? ` · ⭐ ${e.lead_score}` : "";
  const reasons = (e.failure_reasons || []).length
    ? `<div class="reasons">This draft did not pass the automated checks — approving <strong>overrides</strong> them:<ul>${e.failure_reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>`
    : "";
  const approveLabel = e.ready ? "Approve" : "⚠️ Override & approve";
  return `
  <details class="email card" data-id="${esc(e.id)}" data-version="${e.version == null ? "" : esc(e.version)}">
    <summary>${badge} ${i}. ${esc(e.company)} → ${esc(e.contact)}${score}</summary>
    <div class="subject">Subject: ${esc(e.subject)}</div>
    <pre>${esc(e.body)}</pre>
    <div class="status-line">Status: ${STATUS_BADGE[e.status] || ""} <strong>${esc(e.status)}</strong></div>
    ${reasons}
    <div class="controls">
      <button data-act="approve" class="primary">${approveLabel}</button>
      <button data-act="reject">Reject</button>
      <button data-act="edit">Edit</button>
    </div>
    <div class="edit-box hidden">
      <label>Subject<input class="edit-subject" value="${esc(e.subject)}" /></label>
      <label>Body<textarea class="edit-body" rows="6">${esc(e.body)}</textarea></label>
      <button data-act="save-edit" class="primary">Save edit</button>
    </div>
  </details>`;
}

function wireEmailControls(e) {
  const root = document.querySelector(`.email[data-id="${CSS.escape(e.id)}"]`);
  if (!root) return;
  // Optimistic-lock token last rendered for this row; sent so a stale tab 409s
  // instead of clobbering a concurrent change.
  const version = e.version;
  const act = async (action, body) => {
    try {
      if (action === "transition") {
        await api("POST", `/api/runs/${CURRENT_RUN_ID}/emails/${encodeURIComponent(e.id)}/transition`, body);
      } else {
        await api("POST", `/api/runs/${CURRENT_RUN_ID}/emails/${encodeURIComponent(e.id)}/edit`, body);
      }
      await openCampaign(CURRENT_RUN_ID); // reload authoritative state
    } catch (err) {
      alert(err.message);
      await openCampaign(CURRENT_RUN_ID); // resync so the user sees current state
    }
  };
  root.querySelector('[data-act="approve"]').addEventListener("click", () => act("transition", { action: "approve", expected_version: version }));
  root.querySelector('[data-act="reject"]').addEventListener("click", () => act("transition", { action: "reject", expected_version: version }));
  root.querySelector('[data-act="edit"]').addEventListener("click", () =>
    root.querySelector(".edit-box").classList.toggle("hidden"));
  root.querySelector('[data-act="save-edit"]').addEventListener("click", () =>
    act("edit", {
      subject: root.querySelector(".edit-subject").value,
      body: root.querySelector(".edit-body").value,
      expected_version: version,
    }));
}

// --- boot -------------------------------------------------------------------
async function boot() {
  try {
    const me = await api("GET", "/api/auth/me");
    el("whoami").textContent = me.username;
    showApp();
    await loadConfig();
    await loadCampaigns();
  } catch (_) {
    showAuth();
  }
}

boot();
