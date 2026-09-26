"""Central config — model gateway selection and tunable thresholds.
Nothing here should be hardcoded inside a prompt or a node function."""

import os
from dotenv import load_dotenv

load_dotenv()

# --- Model gateway (LLD Section 5 / carried over from cold-chain pattern) ---
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "ANTHROPIC").strip().upper()
MODEL_NAME = os.getenv("MODEL_NAME", "claude-sonnet-5")

# --- Escalation threshold (LLD Section 5) ---
# Starting value per the LLD — not derived yet. Intended to be tuned against
# eval/run_eval.py's false-escalation-rate output once real eval runs exist.
ESCALATION_THRESHOLD = float(os.getenv("ESCALATION_THRESHOLD", "0.75"))

# --- Database (three distinct roles — never an admin/superuser connection
# string for application code) ---
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "pa_agent_poc")

DB_USER = os.getenv("DB_USER", "pa_agent_role")            # decision-making agent
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

DB_INTAKE_USER = os.getenv("DB_INTAKE_USER", "pa_intake_role")  # request submission (UI)
DB_INTAKE_PASSWORD = os.getenv("DB_INTAKE_PASSWORD", "")

DB_ADMIN_USER = os.getenv("DB_ADMIN_USER", "pa_admin_role")     # audit viewer + reviewer decisions
DB_ADMIN_PASSWORD = os.getenv("DB_ADMIN_PASSWORD", "")

DB_CLAIMS_USER = os.getenv("DB_CLAIMS_USER", "claims_agent_role")      # claims decisioning
DB_CLAIMS_PASSWORD = os.getenv("DB_CLAIMS_PASSWORD", "")

DB_CLAIMS_INTAKE_USER = os.getenv("DB_CLAIMS_INTAKE_USER", "claims_intake_role")  # claim submission (UI)
DB_CLAIMS_INTAKE_PASSWORD = os.getenv("DB_CLAIMS_INTAKE_PASSWORD", "")

# --- Policy retrieval ---
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY", "")
POLICY_INDEX_NAME = os.getenv("POLICY_INDEX_NAME", "pa-policy-index")
RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "5"))

# --- Required documents per service code, by request type ---
# Intake completeness is deliberately narrower than the policy's full
# "Required Documentation" list in some cases — see notes.md for why
# (documents whose absence is itself a clinical deny reason per the policy
# text, e.g. missing PT progress notes, are NOT gated at intake; they flow
# to the Decision Agent so the denial carries the correct policy citation).
REQUIRED_DOCS: dict[str, dict[str, list[str]]] = {
    "72148": {  # MRI Lumbar Spine
        "standard": ["physician_progress_note"],
        "expedited": ["physician_progress_note"],
    },
    "A4239": {  # Continuous Glucose Monitor
        "standard": ["physician_order"],
        "expedited": ["physician_order"],
    },
    "97110": {  # Outpatient PT beyond 20 visits
        "standard": ["updated_plan_of_care", "physician_recert"],
        "expedited": ["updated_plan_of_care", "physician_recert"],
    },
    "43644": {  # Bariatric surgery
        "standard": ["bmi_measurement", "weight_program_records", "psych_eval", "informed_consent"],
        "expedited": ["bmi_measurement", "weight_program_records", "psych_eval", "informed_consent"],
    },
}

# ============================================================
# Phase 2 — Claims / Payment Integrity
# ============================================================

CLAIMS_ESCALATION_THRESHOLD = float(os.getenv("CLAIMS_ESCALATION_THRESHOLD", "0.75"))

# Deliberately higher than the escalation threshold — a false SIU flag has
# real provider-relations cost beyond a wasted review cycle (Claims HLD
# Section 7), so the bar to actually flag someone is set more conservatively
# than the bar to merely ask a human to look at something.
SIU_FLAG_THRESHOLD = float(os.getenv("SIU_FLAG_THRESHOLD", "0.85"))

DUPLICATE_CLAIM_WINDOW_DAYS = int(os.getenv("DUPLICATE_CLAIM_WINDOW_DAYS", "14"))
FRAUD_LOOKBACK_WINDOW_DAYS = int(os.getenv("FRAUD_LOOKBACK_WINDOW_DAYS", "90"))
FRAUD_VOLUME_THRESHOLD_MULTIPLIER = float(os.getenv("FRAUD_VOLUME_THRESHOLD_MULTIPLIER", "3.0"))

# Illustrative placeholder amounts — a real payer would supply actual review
# thresholds per service. Used for the threshold-clustering signal in
# fraud_signals.py; unlike the volume-anomaly signal, no synthetic scenario
# currently plants a clustering pattern to test against, so this signal is
# implemented but not yet eval-verified. Documented here rather than silently
# treated as proven.
REVIEW_THRESHOLDS: dict[str, float] = {
    "72148": 1000.0,
    "A4239": 500.0,
    "97110": 300.0,
    "43644": 30000.0,
}

# Same pattern as Phase 1's REQUIRED_DOCS, but flat per service_code — claims
# have no request_type (standard/expedited) distinction. Deliberately narrow:
# richer documentation gaps (e.g. missing progress notes) are the Coverage
# Agent's deny reason, not an intake gate — same design choice Phase 1 made
# and for the same reason (the denial then carries the correct policy
# citation instead of a generic "incomplete" message).
CLAIMS_REQUIRED_DOCS: dict[str, list[str]] = {
    "72148": ["physician_progress_note"],
    "A4239": ["physician_order"],
    "97110": ["updated_plan_of_care", "physician_recert"],
    "43644": ["bmi_measurement", "weight_program_records", "psych_eval", "informed_consent"],
}
