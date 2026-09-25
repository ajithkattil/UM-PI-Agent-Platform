# Prior Authorization Agent — POC

An agentic AI system for a health payer that decides prior authorization
requests: retrieves the relevant coverage policy, evaluates the request
against it, and returns approve / deny-with-citation / escalate — with a
full audit trail and DB-enforced guardrails. Built as Phase 1 of a shared
PA + claims agentic platform (see `docs/01-Usecase-Document.md` Section 10
for what Phase 2 reuses).

## Design documents (`docs/`)

Read in order — each one assumes the last is settled:

1. **[01-Usecase-Document.md](docs/01-Usecase-Document.md)** — problem
   statement, regulatory driver (CMS-0057-F), actors, scope, user scenarios,
   success metrics
2. **[02-High-Level-Design.md](docs/02-High-Level-Design.md)** — architecture,
   components, data stores, orchestration pattern
3. **[03-Low-Level-Design.md](docs/03-Low-Level-Design.md)** — DB schema,
   Pydantic schemas, LangGraph node/edge definitions, tool signatures,
   prompt structure

## What's built vs. what's proven

**[BUILD_NOTES.md](BUILD_NOTES.md)** is the important one before you touch
anything else — it lists exactly what has been run for real (DB guardrails
tested adversarially, intake agent run against all 14 scenarios, the actual
compiled LangGraph run end-to-end with a scripted model, policy chunking run
against the real policy docs) versus what's written but not yet exercised
(real LLM reasoning quality, real Pinecone retrieval — both need API keys
this build environment didn't have).

## Repo structure

```
docs/                       # design documents, read in order above
data/policy/                # 4 coverage policy documents (synthetic)
data/synthetic_requests/    # seed.sql — 14 synthetic PA requests
eval/test_cases.jsonl       # same 14 cases with expected outcomes, for eval
scripts/
  schema.sql                 # Postgres table definitions
  setup_db_roles.sql         # guardrail role grants (pa_agent_role, pa_admin_role)
  generate_synthetic_data.py # regenerates the synthetic data above
  ingest_policy_pinecone.py  # policy chunking + Pinecone upsert (--dry-run works without credentials)
src/
  schemas.py                 # PARequest, PolicyChunk, Decision, GraphState
  config.py                  # model gateway, escalation threshold, required-docs checklist
  audit.py                   # append-only audit log read/write
  orchestrator.py             # LangGraph wiring
  agents/
    intake_agent.py           # documentation completeness check (no LLM needed)
    decision_agent.py         # policy evaluation + citation validation/retry
  tools/
    db_tools.py                # restricted DB queries
    policy_tools.py             # Pinecone retrieval + effective-date filtering
tests/
  test_intake_agent.py         # runs intake against all 14 scenarios via live DB
  test_orchestrator_routing.py # runs the real compiled graph with a scripted model
```

## Setup

```bash
cp .env.example .env   # fill in ANTHROPIC_API_KEY, PINECONE_API_KEY, OPENAI_API_KEY, DB_PASSWORD

# Database
psql -c "CREATE DATABASE pa_agent_poc;"
psql -d pa_agent_poc -f scripts/schema.sql
psql -d pa_agent_poc -f scripts/setup_db_roles.sql
psql -d pa_agent_poc -f data/synthetic_requests/seed.sql

pip install -r requirements.txt

# Verify the DB layer (no API keys needed for this one)
python tests/test_intake_agent.py

# Ingest policy docs (add --dry-run to test chunking without Pinecone)
python scripts/ingest_policy_pinecone.py

# Verify the graph routing logic (uses a scripted fake model, no API keys needed)
python tests/test_orchestrator_routing.py
```

## Next steps

See the end of `BUILD_NOTES.md` — in short: real credentials in, build
`eval/run_eval.py` against the real decision agent, tune the escalation
threshold from real results, then the Streamlit UI.
