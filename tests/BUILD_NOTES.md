# Build Notes — What Was Actually Verified

Everything below was run for real in the build environment, not just written
and assumed to work. Kept here so the distinction between "proven" and "written
but untested" stays honest as the project continues.

## Verified for real

- **Postgres schema + guardrail roles** (`scripts/schema.sql`,
  `scripts/setup_db_roles.sql`): loaded into a live Postgres 16 instance. All 14
  synthetic requests seeded successfully.
- **DB-enforced guardrails, tested adversarially**: connected AS `pa_agent_role`
  and confirmed it can read requests and insert its own decisions/audit
  entries, and confirmed `UPDATE`/`DELETE` on `pa_requests`, `pa_decisions`,
  and `audit_log` are all rejected with `permission denied` — not just granted
  in theory. Same adversarial test run against `pa_admin_role`, confirming it
  can read everything (including the audit log) but cannot write anywhere.
- **Intake / completeness agent** (`src/agents/intake_agent.py`): run against
  all 14 synthetic scenarios via the real, restricted DB connection.
  14/14 correct pending_docs vs. proceeds classification
  (`tests/test_intake_agent.py`).
- **Audit logging**: confirmed the intake test run actually wrote 14 rows to
  `audit_log` with full structured detail, readable via `pa_admin_role`.
- **Policy ingestion / chunking** (`scripts/ingest_policy_pinecone.py --dry-run`):
  run against all 4 real policy documents. Header-aware chunking, effective-date
  extraction, and section-header metadata tagging all confirmed working —
  21 chunks produced across the 4 policies, each correctly tagged.
- **Full graph orchestration** (`src/orchestrator.py`, via
  `tests/test_orchestrator_routing.py`): the actual compiled LangGraph run
  end-to-end with a scripted fake model standing in for the LLM. 6/6 routing
  scenarios correct, including:
  - clean approve and clean deny pass straight through
  - a deny without a citation triggers exactly one retry, and is accepted once
    corrected
  - a deny without a citation on **both** attempts hard-escalates rather than
    ever letting an invalid denial through
  - a stated "approve" below the confidence threshold is overridden to escalate
  - malformed (non-JSON) model output escalates rather than crashing the
    pipeline
- **Real LLM reasoning + real Pinecone retrieval** (`eval/run_eval.py`, run on
  the developer's own machine with real Anthropic/Pinecone/OpenAI keys): all
  14 `eval/test_cases.jsonl` cases run through the actual decision agent.
  **14/14 decision accuracy, 4/4 citation correctness on deny cases, 0/9
  false-escalation rate.** `ESCALATION_THRESHOLD=0.75` required no tuning to
  hit this — both genuinely ambiguous cases (CGM newly-diagnosed, bariatric
  comorbidity-unclear) correctly escalated, and no clean-cut case was
  incorrectly escalated.
  - Getting here required two real fixes caught by this eval run, not
    invented in advance: (1) the ingestion script was extracting
    `service_code` as `"CPT 72148"` while requests used the bare `"72148"`,
    so retrieval silently returned zero chunks every time; (2) the Decision
    Agent's prompt never included the request's actual clinical content
    (only service code + type), so two clinically different requests for the
    same service produced identical decisions. Both are now fixed —
    `PARequest.clinical_notes` carries the case-specific content into the
    prompt.
  - A first pass also showed weak citation specificity (50%, citing bare
    section names like "Coverage Criteria" instead of the specific clause
    violated) — tightened via an explicit prompt instruction, since a vague
    citation doesn't actually satisfy CMS-0057-F's specific-reason
    requirement even when the decision itself is correct.
  - Both fixes and the tightened citation prompt were re-verified after
    adding `clinical_notes` as a real column to `pa_requests` (not just
    passed in directly from the eval JSONL) — same 14/14, 4/4, 0/9 result,
    confirming the DB layer now genuinely supports what eval already proved
    works, not just a shortcut around it.

## Not yet verified — needs real credentials

- **`src/ui.py`** (Streamlit) — not built yet. This is the only remaining
  gap in Phase 1.

## Next steps

1. Build `src/ui.py` (Streamlit) for the submit/decide/escalation-queue flow
2. Revisit `ESCALATION_THRESHOLD` only if real-world usage surfaces a problem
   — no evidence from eval that 0.75 needs adjustment
