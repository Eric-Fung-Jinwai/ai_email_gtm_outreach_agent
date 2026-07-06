"""Streamlit frontend. UI only — all logic lives in ``backend``."""

import streamlit as st

from backend.config import export_provider_env, get_settings
from backend.pipeline import run_pipeline


def main() -> None:
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
                )
                progress.progress(100)
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
            for insight in r.get("insights", [])[:4]:
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
    if emails:
        for i, e in enumerate(emails, 1):
            with st.expander(f"{i}. {e.get('company','')} → {e.get('contact','')}"):
                st.write(f"Subject: {e.get('subject','')}")
                st.text(e.get("body", ""))
    else:
        st.info("No emails generated")


if __name__ == "__main__":
    main()
