"""Streamlit frontend. UI only — all logic lives in ``backend``."""

import streamlit as st

from backend import persistence
from backend.approval import apply_action
from backend.config import export_provider_env, get_settings
from backend.observability import configure_logging
from backend.evaluation.deterministic import evaluate_email
from backend.evaluation.gating import email_is_ready, ready_after_edit
from backend.pipeline import run_pipeline


def main() -> None:
    configure_logging()
    st.set_page_config(page_title="GTM B2B Outreach", layout="wide")

    # Config comes from .env only — never entered in the UI.
    settings = get_settings()
    export_provider_env(settings)
    missing_keys = settings.missing_required_keys()

    # Sidebar: read-only key status
    st.sidebar.header("API Configuration")
    st.sidebar.write(f"OpenAI key: {'✅ loaded' if settings.has_openai_key else '❌ missing'}")
    st.sidebar.write(f"Exa key: {'✅ loaded' if settings.has_exa_key else '❌ missing'}")
    if missing_keys:
        st.sidebar.warning("Set missing key(s) in your .env file: " + ", ".join(missing_keys))
    else:
        st.sidebar.success("All keys loaded from .env")

    _past_campaigns_sidebar()

    # Inputs
    st.title("GTM B2B Outreach Multi Agent Team")
    st.info(
        "GTM teams often need to reach out for demos and discovery calls, but manual research and personalization is slow. "
        "This app uses a multi-agent workflow to find target companies, identify contacts, research genuine insights "
        "(website + Reddit), and generate tailored outreach emails in your chosen style."
    )
    col1, col2 = st.columns(2)
    with col1:
        target_desc = st.text_area("Target companies (industry, size, region, tech, etc.)", height=100)
        offering_desc = st.text_area("Your product/service offering (1-3 sentences)", height=100)
    with col2:
        sender_name = st.text_input("Your name", value="Sales Team")
        sender_company = st.text_input("Your company", value="Our Company")
        calendar_link = st.text_input("Calendar link (optional)", value="")
        num_companies = st.number_input("Number of companies", min_value=1, max_value=10, value=5)
        email_style = st.selectbox(
            "Email style",
            options=["Professional", "Casual", "Cold", "Consultative"],
            index=0,
            help="Choose the tone/format for the generated emails",
        )

    if st.button("Start Outreach", type="primary"):
        if missing_keys:
            st.error("Missing API key(s) in .env: " + ", ".join(missing_keys))
        elif not target_desc or not offering_desc:
            st.error("Please fill in target companies and offering")
        else:
            progress = st.progress(0)
            stage_msg = st.empty()
            details = st.empty()

            def progress_cb(stage: int, total: int, message: str, detail: str = "") -> None:
                stage_msg.info(f"{stage}/{total} {message}")
                progress.progress(int(stage / total * 100) if detail else int((stage - 1) / total * 100))
                if detail:
                    details.write(detail)

            # Suppress companies contacted within the cooldown window (from history).
            suppress = []
            if settings.enable_contact_suppression:
                try:
                    suppress = persistence.recently_contacted_companies(settings.contact_cooldown_days)
                except Exception:
                    suppress = []
                if suppress:
                    st.caption(
                        f"Excluding {len(suppress)} company(ies) contacted in the last "
                        f"{settings.contact_cooldown_days} days."
                    )
            try:
                results = run_pipeline(
                    target_desc=target_desc.strip(),
                    offering_desc=offering_desc.strip(),
                    sender_name=sender_name.strip(),
                    sender_company=sender_company.strip(),
                    calendar_link=calendar_link.strip() or None,
                    num_companies=int(num_companies),
                    email_style=email_style,
                    progress_cb=progress_cb,
                    suppress_companies=suppress,
                )
                progress.progress(100)
                results["calendar_link"] = calendar_link.strip() or None  # for edit re-eval
                # Persist on the main thread, after the pipeline join (single writer).
                try:
                    run_id = persistence.save_run(
                        results,
                        {
                            "target_desc": target_desc.strip(),
                            "offering_desc": offering_desc.strip(),
                            "sender_name": sender_name.strip(),
                            "sender_company": sender_company.strip(),
                            "num_companies": int(num_companies),
                            "email_style": email_style,
                        },
                    )
                    st.session_state["run_id"] = run_id
                except Exception as persist_err:
                    st.warning(f"Results not saved to history: {persist_err}")
                st.session_state["gtm_results"] = results
                stage_msg.success("Completed")
            except Exception as e:
                stage_msg.error("Pipeline failed")
                st.error(f"{e}")

    _render_results()


def _render_results() -> None:
    results = st.session_state.get("gtm_results")
    if not results:
        return

    companies = results.get("companies", [])
    contacts = results.get("contacts", [])
    research = results.get("research", [])
    emails = results.get("emails", [])

    cost = results.get("cost")
    if cost is not None:
        breakdown = results.get("cost_breakdown") or {}
        label = f"💵 Estimated LLM cost this run: ${cost:.4f}"
        if breakdown:
            parts = ", ".join(f"{m}: ${v['cost']:.4f}" for m, v in breakdown.items())
            label += f" ({parts})"
        st.caption(label + "  — prices are estimates; see backend/cost.py")

    timings = results.get("timings")
    if timings:
        total = timings.get("total", 0.0)
        per_stage = ", ".join(f"{k}: {v:.1f}s" for k, v in timings.items() if k != "total")
        st.caption(f"⏱ Total: {total:.1f}s  ({per_stage})")

    st.subheader("Top target companies")
    if companies:
        for idx, c in enumerate(companies, 1):
            st.markdown(f"**{idx}. {c.get('name','')}**  ")
            st.write(c.get("website", ""))
            st.write(c.get("why_fit", ""))
    else:
        st.info("No companies found")
    st.divider()

    st.subheader("Contacts found")
    if contacts:
        for c in contacts:
            st.markdown(f"**{c.get('name','')}**")
            for p in c.get("contacts", [])[:3]:
                inferred = " (inferred)" if p.get("inferred") else ""
                st.write(f"- {p.get('full_name','')} | {p.get('title','')} | {p.get('email','')}{inferred}")
    else:
        st.info("No contacts found")
    st.divider()

    st.subheader("Research insights")
    if research:
        for r in research:
            st.markdown(f"**{r.get('name','')}**")
            for insight in r.get("insights", [])[:6]:
                if isinstance(insight, dict):
                    text = insight.get("text", "")
                    src, url = insight.get("source_type"), insight.get("source_url")
                    suffix = f" ([{src}]({url}))" if url else (f" _({src})_" if src else "")
                    st.write(f"- {text}{suffix}")
                else:
                    st.write(f"- {insight}")
    else:
        st.info("No research insights")
    st.divider()

    st.subheader("Suggested Outreach Emails")
    st.caption(
        "Emails are drafted only for verified contacts at companies with successful "
        "research (no research → no draft, to avoid generic/hallucinated outreach). "
        "Other verified contacts are still listed above for manual outreach. "
        "Inferred (guessed) emails are excluded by default — set "
        "INCLUDE_INFERRED_CONTACTS=true in .env to override."
    )
    if not emails:
        st.info("No emails generated")
        return

    ready = [e for e in emails if email_is_ready(e.get("eval") or {})]
    needs_review = [e for e in emails if not email_is_ready(e.get("eval") or {})]

    if ready:
        for i, e in enumerate(ready, 1):
            _render_email(i, e)
    else:
        st.info("No drafts are ready to send (see Needs review below)")

    if needs_review:
        st.markdown(f"#### ⚠️ Needs review ({len(needs_review)})")
        st.caption(
            "Failed a quality check or the faithfulness judge (unfaithful / unverified) "
            "— not ready to send as-is."
        )
        for i, e in enumerate(needs_review, 1):
            _render_email(i, e)


def _render_email(i: int, e: dict) -> None:
    ev = e.get("eval") or {}
    badge = "✅" if ev.get("passed") else ("⚠️" if ev else "")
    score = e.get("lead_score")
    score_tag = f"  ·  ⭐ {score}" if score is not None else ""
    with st.expander(f"{badge} {i}. {e.get('company','')} → {e.get('contact','')}{score_tag}"):
        st.write(f"Subject: {e.get('subject','')}")
        st.text(e.get("body", ""))
        bd = e.get("lead_score_breakdown")
        if bd:
            st.caption(
                f"Priority {score} — "
                + ", ".join(f"{k}: {v}" for k, v in bd.items())
            )
        failed = [c for c in ev.get("checks", []) if not c.get("passed")]
        if failed:
            st.caption(
                "⚠️ Failed checks: "
                + "; ".join(f"{c['name']} ({c['detail']})" for c in failed)
            )
        elif ev:
            st.caption("✅ Passed all quality checks")

        judge = ev.get("judge")
        if judge:
            if judge.get("error"):
                st.caption(f"⚖️ Faithfulness judge unavailable: {judge['error']}")
            else:
                faith = judge.get("faithful")
                fbadge = "✅ faithful" if faith else ("❌ ungrounded claims" if faith is False else "—")
                st.caption(
                    f"⚖️ {fbadge} · coverage: {judge.get('coverage')} · "
                    f"personalization: {judge.get('personalization')}"
                )
                ungrounded = [c for c in judge.get("claims", []) if not c.get("grounded")]
                if ungrounded:
                    st.caption(
                        "Unsupported claims: " + "; ".join(c.get("claim", "") for c in ungrounded)
                    )
        elif ev.get("judge_stale"):
            st.caption("⚖️ Edited by hand — faithfulness not re-checked; approve overrides.")

        repair = e.get("repair")
        if repair and repair.get("attempted"):
            if repair.get("succeeded"):
                st.caption("🔧 Auto-repaired to remove unsupported claims.")
            else:
                st.caption("🔧 Repair attempted but still unfaithful — needs human review.")

        _approval_controls(e)


_STATUS_BADGE = {"drafted": "📝", "approved": "✅", "rejected": "🚫", "edited": "✏️"}


def _past_campaigns_sidebar() -> None:
    st.sidebar.header("Past campaigns")
    try:
        runs = persistence.list_runs()
    except Exception:
        runs = []
    if not runs:
        st.sidebar.caption("No saved campaigns yet.")
        return
    labels = {
        f"#{r['id']} · {(r['target_desc'] or '')[:28]} · {r['n_emails']} emails": r["id"]
        for r in runs
    }
    choice = st.sidebar.selectbox("Reopen a campaign", ["(current)"] + list(labels.keys()))
    if choice != "(current)" and st.sidebar.button("Load campaign"):
        data = persistence.get_run(labels[choice])
        if data:
            st.session_state["gtm_results"] = data
            st.session_state["run_id"] = data["run_id"]
            st.rerun()


def _persist_status(e: dict) -> None:
    run_id = st.session_state.get("run_id")
    if run_id is None:
        return
    try:
        persistence.update_email_status(run_id, e.get("id"), e.get("status"), e.get("approved_override", False))
    except Exception as ex:  # surface it — don't let the user think it saved
        st.warning(f"Status change was not saved to history: {ex}")


def _persist_edit(e: dict) -> None:
    run_id = st.session_state.get("run_id")
    if run_id is None:
        return
    try:
        persistence.update_email_edit(
            run_id, e.get("id"), e.get("subject", ""), e.get("body", ""), e.get("eval") or {}, e.get("status", "edited")
        )
    except Exception as ex:
        st.warning(f"Edit was not saved to history: {ex}")


def _apply_action(e: dict, action: str) -> bool:
    """Apply a status transition. Returns True on success; on an illegal
    transition it surfaces a warning and returns False (caller skips rerun so
    the warning stays visible)."""
    try:
        e["status"] = apply_action(e.get("status", "drafted"), action)
        return True
    except ValueError as ex:
        st.warning(str(ex))
        return False


def _failure_reasons(e: dict) -> list:
    """Human-readable reasons a draft is not ready, for an informed override."""
    ev = e.get("eval") or {}
    reasons = []
    for c in ev.get("checks", []):
        if not c.get("passed"):
            reasons.append(f"Check `{c['name']}`: {c.get('detail') or 'failed'}")
    judge = ev.get("judge")
    if judge and judge.get("error"):
        reasons.append(f"Faithfulness judge unavailable: {judge['error']}")
    if judge and judge.get("faithful") is False:
        for c in judge.get("claims", []):
            if not c.get("grounded"):
                reasons.append(f"LLM judge — unsupported claim: {c.get('claim', '')}")
        for issue in judge.get("issues", []):
            reasons.append(f"LLM judge: {issue}")
    prior = ev.get("prior_judge")
    if ev.get("judge_stale") and prior and prior.get("faithful") is False:
        claims = "; ".join(c.get("claim", "") for c in prior.get("claims", []) if not c.get("grounded"))
        reasons.append(
            "Before your edit the LLM judge flagged: "
            + (claims or "unfaithful")
            + " (edited text was not re-checked)"
        )
    return reasons


def _approval_controls(e: dict) -> None:
    eid = e.get("id", "0")
    status = e.get("status", "drafted")
    ready = email_is_ready(e.get("eval") or {})
    st.caption(f"Status: {_STATUS_BADGE.get(status, '')} **{status}**")

    if not ready:
        reasons = _failure_reasons(e)
        if reasons:
            st.warning(
                "This draft did not pass the automated checks — approving it "
                "**overrides** them. Reasons:\n" + "\n".join(f"- {r}" for r in reasons)
            )

    c1, c2, c3 = st.columns(3)
    # Not-ready drafts (failed check / unfaithful / edited-unverified) require an
    # explicit, labeled override so the eval gate can't be bypassed by accident.
    approve_label = "Approve" if ready else "⚠️ Override & approve"
    if c1.button(approve_label, key=f"appr-{eid}"):
        if not ready:
            e["approved_override"] = True
        if _apply_action(e, "approve"):
            _persist_status(e)
            st.rerun()
    if c2.button("Reject", key=f"rej-{eid}"):
        if _apply_action(e, "reject"):
            e["approved_override"] = False  # rejected -> any prior override no longer applies
            _persist_status(e)
            st.rerun()
    if c3.button("Edit", key=f"edit-{eid}"):
        st.session_state[f"editing-{eid}"] = not st.session_state.get(f"editing-{eid}", False)

    if st.session_state.get(f"editing-{eid}"):
        new_subject = st.text_input("Edit subject", value=e.get("subject", ""), key=f"subj-{eid}")
        new_body = st.text_area("Edit body", value=e.get("body", ""), key=f"body-{eid}")
        if st.button("Save edit", key=f"save-{eid}"):
            prior_judge = (e.get("eval") or {}).get("judge")
            had_judge = prior_judge is not None
            e["subject"], e["body"] = new_subject, new_body
            cal = (st.session_state.get("gtm_results") or {}).get("calendar_link")
            ev = evaluate_email(e, calendar_link=cal).model_dump()
            ev["judge"] = None  # verdict no longer applies to the edited text
            ev["judge_stale"] = had_judge
            ev["prior_judge"] = prior_judge  # kept so we can show why it was flagged
            # A previously-judged draft is NOT auto-ready after an edit — the new
            # text was never checked for faithfulness (approve overrides).
            ev["ready"] = ready_after_edit(had_judge, ev)
            e["eval"] = ev
            e["approved_override"] = False  # new version -> clear stale override flag
            _apply_action(e, "edit")
            _persist_edit(e)
            st.session_state[f"editing-{eid}"] = False
            st.rerun()


if __name__ == "__main__":
    main()
