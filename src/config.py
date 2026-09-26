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
