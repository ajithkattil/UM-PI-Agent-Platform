"""
Generates synthetic PA request data for the POC.

Deliberately hand-authored scenarios rather than randomly generated fields —
each request is designed to exercise a specific, known-correct outcome against
the policy documents in data/policy/, so the eval harness has ground truth that
actually means something. Random field generation would produce clinically
incoherent combinations (e.g. a CGM request with no diabetes diagnosis) that
can't be scored meaningfully against real policy criteria.

Outputs:
  - data/synthetic_requests/seed.sql   (Postgres seed data matching the LLD schema)
  - eval/test_cases.jsonl              (held-out eval set with expected outcomes)
"""

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SEED_OUT = PROJECT_ROOT / "data" / "synthetic_requests" / "seed.sql"
EVAL_OUT = PROJECT_ROOT / "eval" / "test_cases.jsonl"

# Fixed UUIDs (deterministic — re-running this script produces identical output)
def uid(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))

PLANS = [
    {"plan_id": uid("plan-gold-ppo"), "plan_name": "Gold PPO", "plan_year": 2026},
    {"plan_id": uid("plan-silver-hmo"), "plan_name": "Silver HMO", "plan_year": 2026},
]

PROVIDERS = [
    {"provider_id": uid("prov-1"), "npi": "1234567890", "provider_name": "Dr. Elena Ruiz, Orthopedics"},
    {"provider_id": uid("prov-2"), "npi": "1234567891", "provider_name": "Dr. Marcus Kim, Endocrinology"},
    {"provider_id": uid("prov-3"), "npi": "1234567892", "provider_name": "Dr. Priya Nair, Physical Medicine"},
    {"provider_id": uid("prov-4"), "npi": "1234567893", "provider_name": "Dr. Samuel Osei, Bariatric Surgery"},
]

MEMBERS = [
    {"member_id": uid(f"member-{i}"), "plan_id": PLANS[i % 2]["plan_id"],
     "date_of_birth": f"19{60+i}-0{(i%9)+1}-15", "gender": "F" if i % 2 else "M"}
    for i in range(1, 11)
]

NOW = datetime(2026, 9, 15, 10, 0, 0)


def days_ago(n):
    return (NOW - timedelta(days=n)).strftime("%Y-%m-%d")


# Each scenario: request fields + documents present + expected eval outcome
SCENARIOS = [
    # -- MRI Lumbar Spine (CPT 72148) --
    {
        "label": "mri_approve_standard",
        "service_code": "72148",
        "service_description": "MRI Lumbar Spine without contrast",
        "member_idx": 0, "provider_idx": 0, "request_type": "standard",
        "documents": ["physician_progress_note", "conservative_therapy_record"],
        "clinical_summary": "8 weeks low back pain, 8-week PT trial completed with inadequate response, progress note dated 5 days ago.",
        "expected_outcome": "approve",
        "expected_citation_keywords": ["Coverage Criteria", "72148"],
    },
    {
        "label": "mri_approve_expedited_redflag",
        "service_code": "72148",
        "service_description": "MRI Lumbar Spine without contrast",
        "member_idx": 1, "provider_idx": 0, "request_type": "expedited",
        "documents": ["physician_progress_note"],
        "clinical_summary": "New bowel/bladder dysfunction, suspected cauda equina syndrome, no conservative therapy trial attempted due to urgency.",
        "expected_outcome": "approve",
        "expected_citation_keywords": ["Red Flag", "Expedited Exception"],
    },
    {
        "label": "mri_deny_insufficient_duration",
        "service_code": "72148",
        "service_description": "MRI Lumbar Spine without contrast",
        "member_idx": 2, "provider_idx": 0, "request_type": "standard",
        "documents": ["physician_progress_note"],
        "clinical_summary": "3 weeks low back pain, no conservative therapy trial documented, no red flag findings.",
        "expected_outcome": "deny",
        "expected_citation_keywords": ["Coverage Criteria", "6 weeks"],
    },
    {
        "label": "mri_pending_docs_no_progress_note",
        "service_code": "72148",
        "service_description": "MRI Lumbar Spine without contrast",
        "member_idx": 3, "provider_idx": 0, "request_type": "standard",
        "documents": ["conservative_therapy_record"],
        "clinical_summary": "6 weeks low back pain, PT trial documented, but no physician progress note attached.",
        "expected_outcome": "pending_docs",
        "expected_citation_keywords": ["physician progress note"],
    },
    # -- Continuous Glucose Monitor (HCPCS A4239) --
    {
        "label": "cgm_approve",
        "service_code": "A4239",
        "service_description": "Continuous Glucose Monitor supplies",
        "member_idx": 4, "provider_idx": 1, "request_type": "standard",
        "documents": ["physician_order", "glucose_monitoring_log", "hba1c_result"],
        "clinical_summary": "T2DM on insulin pump, glucose checked 5x/day for 45 days, HbA1c 8.9% above target of 7.0%.",
        "expected_outcome": "approve",
        "expected_citation_keywords": ["Coverage Criteria", "Medical Necessity"],
    },
    {
        "label": "cgm_deny_not_intensive_regimen",
        "service_code": "A4239",
        "service_description": "Continuous Glucose Monitor supplies",
        "member_idx": 5, "provider_idx": 1, "request_type": "standard",
        "documents": ["physician_order", "glucose_monitoring_log"],
        "clinical_summary": "T2DM managed with oral metformin only, no insulin therapy.",
        "expected_outcome": "deny",
        "expected_citation_keywords": ["intensive insulin regimen"],
    },
    {
        "label": "cgm_escalate_newly_diagnosed",
        "service_code": "A4239",
        "service_description": "Continuous Glucose Monitor supplies",
        "member_idx": 6, "provider_idx": 1, "request_type": "expedited",
        "documents": ["physician_order", "glucose_monitoring_log"],
        "clinical_summary": "T1DM diagnosed 10 days ago, started intensive insulin immediately, only 10 days of self-monitoring data available (policy requires 30).",
        "expected_outcome": "escalate",
        "expected_citation_keywords": ["Ambiguous Cases", "clinical judgment"],
    },
    # -- Outpatient Physical Therapy, beyond 20 visits (CPT 97110) --
    {
        "label": "pt_approve_beyond_20",
        "service_code": "97110",
        "service_description": "Outpatient Physical Therapy, visits 21-30",
        "member_idx": 7, "provider_idx": 2, "request_type": "standard",
        "documents": ["progress_notes", "updated_plan_of_care", "physician_recert"],
        "clinical_summary": "20 visits completed with documented functional improvement, updated plan of care with new goals, recert dated 10 days ago.",
        "expected_outcome": "approve",
        "expected_citation_keywords": ["Coverage Criteria", "20 visits"],
    },
    {
        "label": "pt_deny_no_progress_notes",
        "service_code": "97110",
        "service_description": "Outpatient Physical Therapy, visits 21-30",
        "member_idx": 8, "provider_idx": 2, "request_type": "standard",
        "documents": ["updated_plan_of_care", "physician_recert"],
        "clinical_summary": "Requesting additional visits beyond 20, but no progress notes from completed visits submitted.",
        "expected_outcome": "deny",
        "expected_citation_keywords": ["Exclusions", "progress notes"],
    },
    {
        "label": "pt_pending_docs_no_plan_of_care",
        "service_code": "97110",
        "service_description": "Outpatient Physical Therapy, visits 21-30",
        "member_idx": 9, "provider_idx": 2, "request_type": "standard",
        "documents": ["progress_notes"],
        "clinical_summary": "Progress notes submitted but no updated plan of care or physician re-certification.",
        "expected_outcome": "pending_docs",
        "expected_citation_keywords": ["updated plan of care", "physician re-certification"],
    },
    # -- Bariatric Surgery (CPT 43644) --
    {
        "label": "bariatric_approve",
        "service_code": "43644",
        "service_description": "Roux-en-Y Gastric Bypass",
        "member_idx": 0, "provider_idx": 3, "request_type": "standard",
        "documents": ["bmi_measurement", "weight_program_records", "psych_eval", "informed_consent"],
        "clinical_summary": "BMI 42, 6-month physician-supervised weight program completed, psych eval clear, informed consent documented.",
        "expected_outcome": "approve",
        "expected_citation_keywords": ["Coverage Criteria", "BMI"],
    },
    {
        "label": "bariatric_deny_bmi_too_low",
        "service_code": "43644",
        "service_description": "Roux-en-Y Gastric Bypass",
        "member_idx": 1, "provider_idx": 3, "request_type": "standard",
        "documents": ["bmi_measurement", "weight_program_records", "psych_eval", "informed_consent"],
        "clinical_summary": "BMI 33 with hypertension; BMI does not meet the 35 threshold even with a qualifying comorbidity.",
        "expected_outcome": "deny",
        "expected_citation_keywords": ["BMI", "35"],
    },
    {
        "label": "bariatric_escalate_comorbidity_unclear",
        "service_code": "43644",
        "service_description": "Roux-en-Y Gastric Bypass",
        "member_idx": 2, "provider_idx": 3, "request_type": "standard",
        "documents": ["bmi_measurement", "weight_program_records", "psych_eval", "informed_consent"],
        "clinical_summary": "BMI 37, hypertension diagnosis on record from 3 years ago, no current medications or recent labs documenting active management.",
        "expected_outcome": "escalate",
        "expected_citation_keywords": ["active management", "Ambiguous Cases"],
    },
    {
        "label": "bariatric_pending_docs_no_psych_eval",
        "service_code": "43644",
        "service_description": "Roux-en-Y Gastric Bypass",
        "member_idx": 3, "provider_idx": 3, "request_type": "standard",
        "documents": ["bmi_measurement", "weight_program_records", "informed_consent"],
        "clinical_summary": "BMI 44, weight program documented, but no psychological evaluation submitted.",
        "expected_outcome": "pending_docs",
        "expected_citation_keywords": ["psychological evaluation"],
    },
]


def build_seed_sql() -> str:
    lines = ["-- Auto-generated synthetic seed data. Do not use with real PHI.\n"]

    for p in PLANS:
        lines.append(
            f"INSERT INTO plans (plan_id, plan_name, plan_year) VALUES "
            f"('{p['plan_id']}', '{p['plan_name']}', {p['plan_year']});"
        )
    for pr in PROVIDERS:
        lines.append(
            f"INSERT INTO providers (provider_id, npi, provider_name) VALUES "
            f"('{pr['provider_id']}', '{pr['npi']}', '{pr['provider_name']}');"
        )
    for m in MEMBERS:
        lines.append(
            f"INSERT INTO members (member_id, plan_id, date_of_birth, gender) VALUES "
            f"('{m['member_id']}', '{m['plan_id']}', '{m['date_of_birth']}', '{m['gender']}');"
        )

    for i, s in enumerate(SCENARIOS):
        req_id = uid(f"req-{s['label']}")
        member_id = MEMBERS[s["member_idx"]]["member_id"]
        provider_id = PROVIDERS[s["provider_idx"]]["provider_id"]
        submitted = days_ago(2 + i)
        clinical_notes = s["clinical_summary"].replace("'", "''")  # escape for SQL
        lines.append(
            f"INSERT INTO pa_requests (request_id, member_id, provider_id, service_code, "
            f"service_description, submitted_at, status, request_type, clinical_notes) VALUES "
            f"('{req_id}', '{member_id}', '{provider_id}', '{s['service_code']}', "
            f"'{s['service_description']}', '{submitted}', 'submitted', '{s['request_type']}', "
            f"'{clinical_notes}');"
        )
        for doc in s["documents"]:
            doc_id = uid(f"doc-{s['label']}-{doc}")
            lines.append(
                f"INSERT INTO pa_request_documents (document_id, request_id, doc_type, content_ref) VALUES "
                f"('{doc_id}', '{req_id}', '{doc}', 'synthetic:{s['label']}:{doc}');"
            )

    return "\n".join(lines) + "\n"


def build_eval_jsonl() -> str:
    out_lines = []
    for s in SCENARIOS:
        req_id = uid(f"req-{s['label']}")
        member_id = MEMBERS[s["member_idx"]]["member_id"]
        provider_id = PROVIDERS[s["provider_idx"]]["provider_id"]
        case = {
            "request_id": req_id,
            "label": s["label"],
            "member_id": member_id,
            "provider_id": provider_id,
            "service_code": s["service_code"],
            "service_description": s["service_description"],
            "request_type": s["request_type"],
            "documents": s["documents"],
            "clinical_summary": s["clinical_summary"],
            "expected_outcome": s["expected_outcome"],
            "expected_citation_keywords": s["expected_citation_keywords"],
        }
        out_lines.append(json.dumps(case))
    return "\n".join(out_lines) + "\n"


if __name__ == "__main__":
    SEED_OUT.parent.mkdir(parents=True, exist_ok=True)
    EVAL_OUT.parent.mkdir(parents=True, exist_ok=True)

    SEED_OUT.write_text(build_seed_sql())
    EVAL_OUT.write_text(build_eval_jsonl())

    outcome_counts = {}
    for s in SCENARIOS:
        outcome_counts[s["expected_outcome"]] = outcome_counts.get(s["expected_outcome"], 0) + 1

    print(f"Wrote {len(SCENARIOS)} synthetic PA requests:")
    for outcome, count in outcome_counts.items():
        print(f"  {outcome}: {count}")
    print(f"Seed SQL:   {SEED_OUT}")
    print(f"Eval cases: {EVAL_OUT}")
