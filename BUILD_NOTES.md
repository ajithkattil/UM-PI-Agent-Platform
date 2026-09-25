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

## Not yet verified — needs real credentials

- **Actual LLM reasoning quality** — `decision_agent.py`'s real code path
  (`_default_llm_call`, using the Anthropic client) has not been exercised;
  this sandbox has no `ANTHROPIC_API_KEY`. Everything above tested the
  *plumbing around* the LLM call, not the LLM's actual judgment.
- **Real Pinecone retrieval** — `policy_tools.py`'s `upsert_chunks` and
  `retrieve_policy` are written but not run; no `PINECONE_API_KEY` /
  `OPENAI_API_KEY` available here. The chunking logic that feeds it was proven
  via the dry run above.
- **`eval/run_eval.py`** — does not exist yet. Once real credentials are
  available, this is what should run all 14 `eval/test_cases.jsonl` cases
  through the *real* decision agent (not the scripted fake) and score decision
  accuracy, citation correctness, and false-escalation rate, per LLD Section 7.
- **`src/ui.py`** (Streamlit) — not built yet.

## Next steps, in order

1. Add real API keys (`.env`) and run `scripts/ingest_policy_pinecone.py`
   for real (drop `--dry-run`) to populate Pinecone
2. Build `eval/run_eval.py` and run the real decision agent against all 14
   cases — this is the first point where actual LLM reasoning quality gets
   measured, not assumed
3. Tune `ESCALATION_THRESHOLD` based on the false-escalation rate that comes
   back
4. Build `src/ui.py` (Streamlit) for the submit/decide/escalation-queue flow
