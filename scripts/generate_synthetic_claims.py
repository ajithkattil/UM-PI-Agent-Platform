"""
Generates synthetic claims data for Phase 2 eval.

Same discipline as Phase 1's generate_synthetic_data.py: hand-authored
scenarios, not randomly generated fields, so the eval harness has ground
truth that means something. Three things this adds beyond Phase 1's pattern:

1. Several scenarios deliberately reuse the EXACT member/provider/service
   combinations from Phase 1's 14 PA scenarios (same uid() scheme, same seed
   strings) so the PA Cross-Reference Agent has a real Phase 1 decision to
   check against — not an invented one. This only works once Phase 1's
   pa_decisions table has been populated by running eval/run_eval.py (or the
   UI) at least once; it is a documented dependency, not silently assumed.

2. A duplicate-billing pair: the same member+service+date_of_service
   submitted twice.

3. A deliberately planted, objectively-measurable volume anomaly: one
   provider (Dr. Elena Ruiz) has far more claims for one service in the
   fraud lookback window than any other provider — not a vague "looks
   suspicious" case. BACKGROUND_CLAIMS establishes the peer baseline;
   SCENARIOS' flag_siu case is the claim that should trip the signal against
   that baseline.

Outputs:
  - data/claims/seed_claims.sql          (Postgres seed data)
  - eval/claims_test_cases.jsonl          (held-out eval set, SCENARIOS only —
                                            BACKGROUND_CLAIMS are context, not
                                            individually scored)
"""

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SEED_OUT = PROJECT_ROOT / "data" / "claims" / "seed_claims.sql"
EVAL_OUT = PROJECT_ROOT / "eval" / "claims_test_cases.jsonl"


def uid(seed: str) -> str:
    """Same uuid5 scheme as Phase 1's generate_synthetic_data.py — this is
    what makes referencing Phase 1's members/providers/PA-requests by label
    ('member-1', 'req-mri_approve_standard', etc.) resolve to the SAME
    UUIDs already sitting in the database, not new/unrelated ones."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))


# Phase 1's member/provider indexing (see generate_synthetic_data.py) —
# member-1..10, prov-1..4. Referenced here by the same seed strings.
def member_id(i: int) -> str:
    return uid(f"member-{i}")


def provider_id(i: int) -> str:
    return uid(f"prov-{i}")


def pa_request_id(label: str) -> str:
    """Resolves to the exact request_id of one of Phase 1's 14 PA scenarios."""
    return uid(f"req-{label}")


PROVIDERS_NEW = [
    {"provider_id": uid("prov-5"), "npi": "1234567894", "provider_name": "Dr. Aisha Patel, Orthopedics"},
]

CLAIMS_NOW = datetime(2026, 9, 24, 9, 0, 0)


def days_ago(n):
    return (CLAIMS_NOW - timedelta(days=n)).strftime("%Y-%m-%d")


def ts_days_ago(n):
    return (CLAIMS_NOW - timedelta(days=n)).strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# BACKGROUND CLAIMS — establish a realistic peer baseline.
# Not individually eval-scored; they exist so the volume anomaly in
# SCENARIOS is measurable against something real, not asserted.
# Every other provider gets 1 claim for 72148 in the lookback window —
# provider 1 (Dr. Elena Ruiz) gets 7, deliberately far above that baseline.
# ============================================================
BACKGROUND_CLAIMS = [
    {"label": f"bg_mri_provider2_{i}", "member_i": 5 + i, "provider_i": 2,
     "service_code": "72148", "service_description": "MRI Lumbar Spine without contrast",
     "billed_amount": 850.00, "days_ago_service": 20 + i * 10}
    for i in range(1)
] + [
    {"label": f"bg_mri_provider3_{i}", "member_i": 4 + i, "provider_i": 3,
     "service_code": "72148", "service_description": "MRI Lumbar Spine without contrast",
     "billed_amount": 850.00, "days_ago_service": 15 + i * 10}
    for i in range(1)
] + [
    {"label": f"bg_mri_provider5_{i}", "member_i": (i % 10) + 1, "provider_i": 5,
     "service_code": "72148", "service_description": "MRI Lumbar Spine without contrast",
     "billed_amount": 850.00, "days_ago_service": 5 + i * 8}
    for i in range(7)  # the anomalous provider — 7 claims vs. peers' 1 each.
    # Deliberately NOT provider 1 (Dr. Elena Ruiz) — she's also the provider
    # for claim_pay_matches_approved_mri and claim_deny_pa_was_denied, and
    # fraud_signals.compute_signals() operates at the provider+service
    # level, not per-claim. Reusing her for the anomaly would have made
    # EVERY claim from her inherit the fraud signal, contaminating two
    # scenarios that were never meant to test fraud logic at all — which is
    # exactly what happened before this fix (see SIU_FLAG_THRESHOLD tuning
    # notes / eval history).
]

# ============================================================
# SCENARIOS — individually eval-scored, one per outcome type
# ============================================================
SCENARIOS = [
    {
        "label": "claim_pay_matches_approved_mri",
        "member_i": 1, "provider_i": 1, "service_code": "72148",
        "service_description": "MRI Lumbar Spine without contrast",
        "billed_amount": 850.00, "days_ago_service": 3,
        "documents": ["physician_progress_note", "conservative_therapy_record"],
        "clinical_notes": "8 weeks low back pain, 8-week PT trial completed with inadequate response, progress note dated 5 days ago.",
        "linked_pa_label": "mri_approve_standard",
        "expected_outcome": "pay",
    },
    {
        "label": "claim_pay_matches_approved_cgm",
        "member_i": 5, "provider_i": 2, "service_code": "A4239",
        "service_description": "Continuous Glucose Monitor supplies",
        "billed_amount": 120.00, "days_ago_service": 4,
        "documents": ["physician_order", "glucose_monitoring_log", "hba1c_result"],
        "clinical_notes": "T2DM on insulin pump, glucose checked 5x/day for 45 days, HbA1c 8.9% above target of 7.0%.",
        "linked_pa_label": "cgm_approve",
        "expected_outcome": "pay",
    },
    {
        "label": "claim_deny_pa_was_denied",
        "member_i": 3, "provider_i": 1, "service_code": "72148",
        "service_description": "MRI Lumbar Spine without contrast",
        "billed_amount": 850.00, "days_ago_service": 6,
        "documents": ["physician_progress_note"],
        "clinical_notes": "3 weeks low back pain, no conservative therapy trial documented, no red flag findings.",
        "linked_pa_label": "mri_deny_insufficient_duration",  # this PA was DENIED in Phase 1
        "expected_outcome": "deny",
        "expected_citation_keywords": ["prior authorization", "denied", "PA"],
    },
    {
        "label": "claim_deny_no_pa_on_file",
        "member_i": 6, "provider_i": 4, "service_code": "43644",
        "service_description": "Roux-en-Y Gastric Bypass",
        "billed_amount": 25000.00, "days_ago_service": 10,
        "documents": ["bmi_measurement", "weight_program_records", "psych_eval", "informed_consent"],
        "clinical_notes": "BMI 41, procedure performed. No prior authorization was obtained beforehand.",
        "linked_pa_label": None,  # deliberately no matching PA request exists
        "expected_outcome": "deny",
        "expected_citation_keywords": ["prior authorization", "required", "no PA"],
    },
    {
        "label": "claim_deny_coverage_independent_of_pa",
        "member_i": 8, "provider_i": 3, "service_code": "97110",
        "service_description": "Outpatient Physical Therapy, visits 21-30",
        "billed_amount": 180.00, "days_ago_service": 7,
        "documents": ["updated_plan_of_care", "physician_recert"],  # progress_notes deliberately missing
        "clinical_notes": "Billing for visits 21-25. No progress notes submitted showing continued functional improvement despite an approved PA on file.",
        "linked_pa_label": "pt_approve_beyond_20",  # PA approved — tests coverage denies independently
        "expected_outcome": "deny",
        "expected_citation_keywords": ["progress notes", "Exclusions"],
    },
    {
        "label": "claim_duplicate_original",
        "member_i": 5, "provider_i": 2, "service_code": "A4239",
        "service_description": "Continuous Glucose Monitor supplies",
        "billed_amount": 120.00, "days_ago_service": 4,  # SAME date_of_service as claim_pay_matches_approved_cgm
        "submitted_days_ago": 1,  # submitted well AFTER the original (submitted_days_ago=3) —
                                   # this is what makes it unambiguously "the duplicate," not a tie
        "documents": ["physician_order", "glucose_monitoring_log", "hba1c_result"],
        "clinical_notes": "Duplicate submission of the same CGM supply claim already billed.",
        "linked_pa_label": "cgm_approve",
        "expected_outcome": "duplicate",
    },
    {
        "label": "claim_escalate_ambiguous",
        "member_i": 7, "provider_i": 2, "service_code": "A4239",
        "service_description": "Continuous Glucose Monitor supplies",
        "billed_amount": 120.00, "days_ago_service": 5,
        "documents": ["physician_order", "glucose_monitoring_log"],
        "clinical_notes": "T1DM diagnosed 10 days ago, started intensive insulin immediately, only 10 days of self-monitoring data available.",
        "linked_pa_label": "cgm_escalate_newly_diagnosed",  # this PA was itself ESCALATED
        "expected_outcome": "escalate",
    },
    {
        "label": "claim_flag_siu_volume_anomaly",
        "member_i": 9, "provider_i": 5, "service_code": "72148",  # provider 5 = the isolated anomalous provider
        "service_description": "MRI Lumbar Spine without contrast",
        "billed_amount": 850.00, "days_ago_service": 2,
        "documents": ["physician_progress_note", "conservative_therapy_record"],
        "clinical_notes": "8 weeks low back pain, conservative therapy trial documented, progress note current.",
        "linked_pa_label": None,
        "expected_outcome": "flag_siu",
        # Individually this claim looks clean — the flag is ONLY correct
        # because of this provider's claim volume in BACKGROUND_CLAIMS.
        # This is the case that tests whether the Fraud Agent is actually
        # using computed history, not just re-deciding coverage.
    },
]


def build_claim_insert(claim_id, member_i, provider_i, service_code,
                        service_description, billed_amount, days_ago_service,
                        clinical_notes, doc_status="submitted",
                        submitted_days_ago=None):
    member = member_id(member_i)
    provider = provider_id(provider_i)
    date_of_service = days_ago(days_ago_service)
    # Normally submitted shortly after service; submitted_days_ago lets a
    # scenario override this explicitly (needed for the duplicate pair,
    # where both claims share the same date_of_service but must have
    # genuinely different submitted_at times for "which one is the
    # duplicate" to be well-defined rather than a tie).
    submitted_offset = submitted_days_ago if submitted_days_ago is not None else max(days_ago_service - 1, 0)
    submitted_at = ts_days_ago(submitted_offset)
    notes = clinical_notes.replace("'", "''")
    return (
        f"INSERT INTO claims (claim_id, member_id, provider_id, service_code, "
        f"service_description, billed_amount, date_of_service, submitted_at, status, clinical_notes) VALUES "
        f"('{claim_id}', '{member}', '{provider}', '{service_code}', "
        f"'{service_description}', {billed_amount}, '{date_of_service}', '{submitted_at}', "
        f"'{doc_status}', '{notes}') "
        f"ON CONFLICT (claim_id) DO NOTHING;"
    )


def build_seed_sql() -> str:
    lines = ["-- Auto-generated synthetic claims. Do not use with real PHI.\n"]
    lines.append("-- New provider, isolated specifically for the fraud/SIU test scenario\n")
    lines.append("-- (see PROVIDERS_NEW comment above for why this can't reuse an existing one)\n")
    for p in PROVIDERS_NEW:
        lines.append(
            f"INSERT INTO providers (provider_id, npi, provider_name) VALUES "
            f"('{p['provider_id']}', '{p['npi']}', '{p['provider_name']}') "
            f"ON CONFLICT (provider_id) DO NOTHING;"
        )

    lines.append("\n-- Background claims: establish peer claim-volume baseline (not individually eval-scored)\n")

    for bg in BACKGROUND_CLAIMS:
        claim_id = uid(f"claim-{bg['label']}")
        lines.append(build_claim_insert(
            claim_id, bg["member_i"], bg["provider_i"], bg["service_code"],
            bg["service_description"], bg["billed_amount"], bg["days_ago_service"],
            clinical_notes="Background claim history for fraud-signal peer baseline.",
        ))

    lines.append("\n-- Scored scenarios (see eval/claims_test_cases.jsonl for expected outcomes)\n")

    for s in SCENARIOS:
        claim_id = uid(f"claim-{s['label']}")
        lines.append(build_claim_insert(
            claim_id, s["member_i"], s["provider_i"], s["service_code"],
            s["service_description"], s["billed_amount"], s["days_ago_service"],
            s["clinical_notes"], submitted_days_ago=s.get("submitted_days_ago"),
        ))
        for doc in s["documents"]:
            doc_id = uid(f"claimdoc-{s['label']}-{doc}")
            lines.append(
                f"INSERT INTO claim_documents (document_id, claim_id, doc_type, content_ref) VALUES "
                f"('{doc_id}', '{claim_id}', '{doc}', 'synthetic:{s['label']}:{doc}') "
                f"ON CONFLICT (document_id) DO NOTHING;"
            )

    return "\n".join(lines) + "\n"


def build_eval_jsonl() -> str:
    out_lines = []
    for s in SCENARIOS:
        claim_id = uid(f"claim-{s['label']}")
        case = {
            "claim_id": claim_id,
            "label": s["label"],
            "member_id": member_id(s["member_i"]),
            "provider_id": provider_id(s["provider_i"]),
            "service_code": s["service_code"],
            "service_description": s["service_description"],
            "billed_amount": s["billed_amount"],
            "date_of_service": days_ago(s["days_ago_service"]),
            "documents": s["documents"],
            "clinical_notes": s["clinical_notes"],
            "linked_pa_request_id": pa_request_id(s["linked_pa_label"]) if s.get("linked_pa_label") else None,
            "expected_outcome": s["expected_outcome"],
            "expected_citation_keywords": s.get("expected_citation_keywords", []),
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

    print(f"Wrote {len(BACKGROUND_CLAIMS)} background claim(s) + {len(SCENARIOS)} scored scenario(s):")
    for outcome, count in outcome_counts.items():
        print(f"  {outcome}: {count}")
    print(f"Seed SQL:   {SEED_OUT}")
    print(f"Eval cases: {EVAL_OUT}")
    print()
    print("NOTE: scenarios referencing linked_pa_label require Phase 1's")
    print("pa_decisions table to already be populated (run eval/run_eval.py")
    print("or the UI first) — the PA Cross-Reference Agent needs a real")
    print("decision to find, not just a request to exist.")
