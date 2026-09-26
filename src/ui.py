"""Streamlit UI for the PA Agent POC.

Four tabs: submit a new request (runs it through the real graph immediately —
a POC simplification; a production system would process asynchronously),
browse requests and their decisions, resolve escalated cases as a human
reviewer, and view the audit trail. Each tab uses the DB role that actually
matches who's doing the action (see src/tools/db_tools.py's module docstring)
— this UI does not have its own elevated credentials.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from src.schemas import PARequest, GraphState
from src.orchestrator import build_graph
from src.tools import db_tools
from src.audit import get_audit_trail
from src import config

st.set_page_config(page_title="PA Agent POC", layout="wide")

# Known services — the four policies this POC has coverage docs for. A real
# system would look this up from a service/policy catalog; hardcoded here
# since Phase 1 targets a single hypothetical payer's fixed policy set
# (Use Case Document, Section 9).
SERVICES = {
    "72148": {
        "description": "MRI Lumbar Spine without contrast",
        "documents": ["physician_progress_note", "conservative_therapy_record"],
    },
    "A4239": {
        "description": "Continuous Glucose Monitor supplies",
        "documents": ["physician_order", "glucose_monitoring_log", "hba1c_result"],
    },
    "97110": {
        "description": "Outpatient Physical Therapy, visits beyond 20",
        "documents": ["progress_notes", "updated_plan_of_care", "physician_recert"],
    },
    "43644": {
        "description": "Roux-en-Y Gastric Bypass",
        "documents": ["bmi_measurement", "weight_program_records", "psych_eval", "informed_consent"],
    },
}

tab_submit, tab_requests, tab_review, tab_audit = st.tabs(
    ["Submit Request", "Requests & Decisions", "Reviewer Queue", "Audit Log (Admin)"]
)

# --- Tab 1: Submit Request ---
with tab_submit:
    st.subheader("Submit a Prior Authorization Request")

    try:
        members = db_tools.list_members()
        providers = db_tools.list_providers()
    except Exception as e:
        st.error(f"Could not load members/providers from the database: {e}")
        members, providers = [], []

    if members and providers:
        col1, col2 = st.columns(2)
        with col1:
            member_choice = st.selectbox(
                "Member",
                options=members,
                format_func=lambda m: f"{m['member_id'][:8]}... ({m['plan_name']})",
            )
            service_code = st.selectbox(
                "Service",
                options=list(SERVICES.keys()),
                format_func=lambda code: f"{code} — {SERVICES[code]['description']}",
            )
            request_type = st.radio("Request type", ["standard", "expedited"], horizontal=True)
        with col2:
            provider_choice = st.selectbox(
                "Provider", options=providers, format_func=lambda p: p["provider_name"],
            )
            documents = st.multiselect(
                "Documents attached",
                options=SERVICES[service_code]["documents"],
                default=[],
                help="Which supporting documents are you attaching to this request?",
            )

        clinical_notes = st.text_area(
            "Clinical notes",
            placeholder="Describe the clinical situation — this is what the Decision Agent "
                        "actually reasons against, not just the service code.",
            height=120,
        )

        if st.button("Submit Request", type="primary"):
            if not clinical_notes.strip():
                st.warning("Clinical notes are empty — the agent will have almost nothing "
                           "case-specific to reason about and will likely escalate.")

            request_id = db_tools.create_pa_request(
                member_id=member_choice["member_id"],
                provider_id=provider_choice["provider_id"],
                service_code=service_code,
                service_description=SERVICES[service_code]["description"],
                request_type=request_type,
                clinical_notes=clinical_notes,
                documents=documents,
            )
            st.success(f"Request submitted: `{request_id}`")

            with st.spinner("Running through the agent..."):
                request_obj = PARequest(
                    request_id=request_id,
                    member_id=member_choice["member_id"],
                    provider_id=provider_choice["provider_id"],
                    service_code=service_code,
                    service_description=SERVICES[service_code]["description"],
                    request_type=request_type,
                    documents=documents,
                    clinical_notes=clinical_notes,
                )
                graph = build_graph()
                result = graph.invoke(GraphState(request=request_obj))

            decision = result.get("decision")
            if decision is None:
                st.info(f"**Pending documentation** — missing: {', '.join(result.get('missing_documents', []))}")
            else:
                outcome = decision.outcome if hasattr(decision, "outcome") else decision["outcome"]
                citation = decision.citation if hasattr(decision, "citation") else decision["citation"]
                confidence = decision.confidence if hasattr(decision, "confidence") else decision["confidence"]
                color = {"approve": "green", "deny": "red", "escalate": "orange"}[outcome]
                st.markdown(f"### Decision: :{color}[{outcome.upper()}]")
                st.caption(f"Confidence: {confidence:.2f}")
                if citation:
                    st.write(f"**Citation:** {citation}")
    else:
        st.info("No members/providers found — make sure the database is seeded "
                "(see README Setup, step 5) and DB_INTAKE_PASSWORD is set in .env.")

# --- Tab 2: Requests & Decisions ---
with tab_requests:
    st.subheader("All Requests")
    if st.button("Refresh", key="refresh_requests"):
        st.rerun()
    try:
        rows = db_tools.list_requests_with_decisions()
        if rows:
            st.dataframe(rows, use_container_width=True)
        else:
            st.info("No requests yet — submit one in the first tab.")
    except Exception as e:
        st.error(f"Could not load requests: {e}")

# --- Tab 3: Reviewer Queue ---
with tab_review:
    st.subheader("Escalated Cases Awaiting Human Review")
    try:
        pending = db_tools.list_escalated_awaiting_review()
    except Exception as e:
        st.error(f"Could not load the reviewer queue: {e}")
        pending = []

    if not pending:
        st.info("Nothing escalated right now.")
    for case in pending:
        with st.expander(f"{case['service_code']} — {case['service_description']} "
                          f"(confidence {case['confidence']:.2f})"):
            st.write(f"**Request ID:** `{case['request_id']}`")
            st.write(f"**Clinical notes:** {case['clinical_notes']}")
            st.write(f"**Agent's citation/reasoning:** {case['citation']}")

            reviewer_id = st.text_input("Reviewer ID", key=f"reviewer_{case['request_id']}")
            col1, col2 = st.columns(2)
            with col1:
                review_citation = st.text_input("Citation / reason", key=f"citation_{case['request_id']}")
            with col2:
                review_outcome = st.selectbox("Decision", ["approve", "deny"], key=f"outcome_{case['request_id']}")

            if st.button("Submit Review", key=f"submit_{case['request_id']}"):
                if not reviewer_id.strip():
                    st.warning("Enter a reviewer ID before submitting.")
                else:
                    db_tools.record_reviewer_decision(
                        request_id=case["request_id"],
                        outcome=review_outcome,
                        citation=review_citation or "(reviewer decision, no citation given)",
                        reviewer_id=reviewer_id,
                    )
                    # st.success() here gets wiped out by the st.rerun() below
                    # before it's ever visible — st.toast() is built to
                    # survive exactly this pattern (show feedback, then
                    # rerun to refresh the list).
                    st.toast(f"Reviewer decision recorded for {case['request_id'][:8]}...", icon="✅")
                    st.rerun()

# --- Tab 4: Audit Log (Admin) ---
with tab_audit:
    st.subheader("Audit Trail")
    request_id_lookup = st.text_input("Request ID (full or partial)")
    if request_id_lookup:
        try:
            rows = db_tools.list_requests_with_decisions()
            matches = [r for r in rows if request_id_lookup in r["request_id"]]
            if not matches:
                st.info("No matching request found.")
            for m in matches:
                st.write(f"**{m['request_id']}** — {m['service_code']} ({m.get('outcome', 'no decision yet')})")
                trail = get_audit_trail(m["request_id"])
                for entry in trail:
                    st.json({"step": entry["step"], "detail": entry["detail"], "at": entry["created_at"]})
        except Exception as e:
            st.error(f"Could not load audit trail: {e}")
    else:
        st.caption("Enter a request ID above to view its full audit trail.")
