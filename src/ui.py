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
from src.schemas import PARequest, GraphState, ClaimRequest, ClaimGraphState
from src.orchestrator import build_graph
from src.claims_orchestrator import build_claims_graph
from src.tools import db_tools, claims_db_tools
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

tab_submit, tab_requests, tab_review, tab_claim_submit, tab_claims, tab_siu, tab_examiner, tab_audit = st.tabs(
    ["Submit Request", "Requests & Decisions", "Reviewer Queue",
     "Submit Claim", "Claims & Decisions", "SIU Queue", "Claims Examiner Queue",
     "Audit Log (Admin)"]
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

# --- Tab 4: Submit Claim ---
with tab_claim_submit:
    st.subheader("Submit a Claim")

    try:
        members = db_tools.list_members()
        providers = db_tools.list_providers()
    except Exception as e:
        st.error(f"Could not load members/providers from the database: {e}")
        members, providers = [], []

    if members and providers:
        col1, col2 = st.columns(2)
        with col1:
            c_member = st.selectbox(
                "Member", options=members,
                format_func=lambda m: f"{m['member_id'][:8]}... ({m['plan_name']})",
                key="claim_member",
            )
            c_service_code = st.selectbox(
                "Service", options=list(SERVICES.keys()),
                format_func=lambda code: f"{code} — {SERVICES[code]['description']}",
                key="claim_service",
            )
            c_billed_amount = st.number_input("Billed amount ($)", min_value=0.0, value=100.0, step=10.0)
        with col2:
            c_provider = st.selectbox(
                "Provider", options=providers, format_func=lambda p: p["provider_name"],
                key="claim_provider",
            )
            c_date_of_service = st.date_input("Date of service")
            c_documents = st.multiselect(
                "Documents attached",
                options=SERVICES[c_service_code]["documents"], default=[],
                key="claim_documents",
            )

        c_clinical_notes = st.text_area(
            "Clinical notes", key="claim_clinical_notes", height=120,
            placeholder="Same principle as PA requests — this is what the Coverage and Fraud "
                        "agents actually reason against.",
        )

        if st.button("Submit Claim", type="primary"):
            claim_id = claims_db_tools.create_claim(
                member_id=c_member["member_id"], provider_id=c_provider["provider_id"],
                service_code=c_service_code, service_description=SERVICES[c_service_code]["description"],
                billed_amount=c_billed_amount, date_of_service=c_date_of_service,
                clinical_notes=c_clinical_notes, documents=c_documents,
            )
            st.success(f"Claim submitted: `{claim_id}`")

            with st.spinner("Running through the claims graph (Coverage + PA Cross-Reference + Fraud)..."):
                claim_obj = ClaimRequest(
                    claim_id=claim_id, member_id=c_member["member_id"], provider_id=c_provider["provider_id"],
                    service_code=c_service_code, service_description=SERVICES[c_service_code]["description"],
                    billed_amount=c_billed_amount, date_of_service=c_date_of_service,
                    documents=c_documents, clinical_notes=c_clinical_notes,
                )
                graph = build_claims_graph()
                result = graph.invoke(ClaimGraphState(claim=claim_obj))

            decision = result.get("decision")
            status = result.get("status")
            if decision is None:
                if status == "duplicate":
                    st.warning(f"**Duplicate** — matches an existing claim already on file "
                               f"(`{result.get('duplicate_of_claim_id')}`).")
                else:
                    st.info(f"**Pending documentation** — missing: {', '.join(result.get('missing_documents', []))}")
            else:
                outcome = decision.outcome if hasattr(decision, "outcome") else decision["outcome"]
                citation = decision.citation if hasattr(decision, "citation") else decision["citation"]
                confidence = decision.confidence if hasattr(decision, "confidence") else decision["confidence"]
                color = {"pay": "green", "deny": "red", "flag_siu": "orange", "escalate": "orange"}[outcome]
                st.markdown(f"### Decision: :{color}[{outcome.upper()}]")
                st.caption(f"Confidence: {confidence:.2f}")
                if citation:
                    st.write(f"**Citation:** {citation}")
    else:
        st.info("No members/providers found — check the database is seeded.")

# --- Tab 5: Claims & Decisions ---
with tab_claims:
    st.subheader("All Claims")
    if st.button("Refresh", key="refresh_claims"):
        st.rerun()
    try:
        rows = claims_db_tools.list_claims_with_decisions()
        if rows:
            st.dataframe(rows, use_container_width=True)
        else:
            st.info("No claims yet — submit one in the Submit Claim tab.")
    except Exception as e:
        st.error(f"Could not load claims: {e}")

# --- Tab 6: SIU Queue ---
with tab_siu:
    st.subheader("Claims Flagged for SIU Investigation")
    st.caption("Suspected fraud/waste/abuse — kept separate from the ordinary "
               "Claims Examiner Queue since these carry different stakes and go to a different reviewer.")
    try:
        siu_cases = claims_db_tools.list_siu_queue()
    except Exception as e:
        st.error(f"Could not load the SIU queue: {e}")
        siu_cases = []

    if not siu_cases:
        st.info("Nothing flagged for SIU right now.")
    for case in siu_cases:
        with st.expander(f"{case['service_code']} — {case['service_description']} "
                          f"(${case['billed_amount']:.2f}, confidence {case['confidence']:.2f})"):
            st.write(f"**Claim ID:** `{case['claim_id']}`")
            st.write(f"**Clinical notes:** {case['clinical_notes']}")
            st.write(f"**Fraud signal:** {case['fraud_signal']}")

            investigator_id = st.text_input("Investigator ID", key=f"siu_investigator_{case['claim_id']}")
            col1, col2 = st.columns(2)
            with col1:
                siu_citation = st.text_input("Findings / reason", key=f"siu_citation_{case['claim_id']}")
            with col2:
                siu_outcome = st.selectbox("Decision", ["pay", "deny"], key=f"siu_outcome_{case['claim_id']}")

            if st.button("Submit Investigation Result", key=f"siu_submit_{case['claim_id']}"):
                if not investigator_id.strip():
                    st.warning("Enter an investigator ID before submitting.")
                else:
                    claims_db_tools.record_claim_reviewer_decision(
                        claim_id=case["claim_id"], outcome=siu_outcome,
                        citation=siu_citation or "(SIU decision, no findings given)",
                        reviewer_id=investigator_id,
                    )
                    st.toast(f"SIU decision recorded for {case['claim_id'][:8]}...", icon="✅")
                    st.rerun()

# --- Tab 7: Claims Examiner Queue ---
with tab_examiner:
    st.subheader("Ambiguous Claims Awaiting Examiner Review")
    st.caption("Genuinely ambiguous single-claim cases — not suspected fraud. See SIU Queue for that.")
    try:
        examiner_cases = claims_db_tools.list_claims_examiner_queue()
    except Exception as e:
        st.error(f"Could not load the claims examiner queue: {e}")
        examiner_cases = []

    if not examiner_cases:
        st.info("Nothing awaiting examiner review right now.")
    for case in examiner_cases:
        with st.expander(f"{case['service_code']} — {case['service_description']} "
                          f"(${case['billed_amount']:.2f}, confidence {case['confidence']:.2f})"):
            st.write(f"**Claim ID:** `{case['claim_id']}`")
            st.write(f"**Clinical notes:** {case['clinical_notes']}")
            st.write(f"**Coverage signal:** {case['coverage_signal']}")
            st.write(f"**PA cross-reference signal:** {case['pa_xref_signal']}")

            examiner_id = st.text_input("Examiner ID", key=f"examiner_{case['claim_id']}")
            col1, col2 = st.columns(2)
            with col1:
                examiner_citation = st.text_input("Citation / reason", key=f"examiner_citation_{case['claim_id']}")
            with col2:
                examiner_outcome = st.selectbox("Decision", ["pay", "deny"], key=f"examiner_outcome_{case['claim_id']}")

            if st.button("Submit Review", key=f"examiner_submit_{case['claim_id']}"):
                if not examiner_id.strip():
                    st.warning("Enter an examiner ID before submitting.")
                else:
                    claims_db_tools.record_claim_reviewer_decision(
                        claim_id=case["claim_id"], outcome=examiner_outcome,
                        citation=examiner_citation or "(examiner decision, no citation given)",
                        reviewer_id=examiner_id,
                    )
                    st.toast(f"Examiner decision recorded for {case['claim_id'][:8]}...", icon="✅")
                    st.rerun()

# --- Tab 8: Audit Log (Admin) ---
with tab_audit:
    st.subheader("Audit Trail")
    st.caption("Works for both Prior Authorization requests and Claims — the audit log is shared.")
    request_id_lookup = st.text_input("Request or Claim ID (full or partial)")
    if request_id_lookup:
        try:
            pa_rows = db_tools.list_requests_with_decisions()
            claim_rows = claims_db_tools.list_claims_with_decisions()
            pa_matches = [r for r in pa_rows if request_id_lookup in r["request_id"]]
            claim_matches = [r for r in claim_rows if request_id_lookup in r["claim_id"]]
            if not pa_matches and not claim_matches:
                st.info("No matching request or claim found.")
            for m in pa_matches:
                st.write(f"**PA Request `{m['request_id']}`** — {m['service_code']} ({m.get('outcome', 'no decision yet')})")
                trail = get_audit_trail(m["request_id"])
                for entry in trail:
                    st.json({"step": entry["step"], "detail": entry["detail"], "at": entry["created_at"]})
            for m in claim_matches:
                st.write(f"**Claim `{m['claim_id']}`** — {m['service_code']} ({m.get('outcome', 'no decision yet')})")
                trail = get_audit_trail(m["claim_id"])
                for entry in trail:
                    st.json({"step": entry["step"], "detail": entry["detail"], "at": entry["created_at"]})
        except Exception as e:
            st.error(f"Could not load audit trail: {e}")
    else:
        st.caption("Enter a request ID above to view its full audit trail.")
