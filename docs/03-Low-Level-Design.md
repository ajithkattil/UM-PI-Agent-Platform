# Prior Authorization Agent — Low-Level Design (LLD)

**Project**: Agentic AI Payer Platform — Phase 1 (Prior Authorization)
**Status**: Draft — implements the linear design locked in the HLD
**Scope**: Single hypothetical payer, synthetic data, no PHI

---

## 1. Repository Layout

```
pa-agent-poc/
├── .env.example
├── docker-compose.yml            # Postgres + app
├── requirements.txt
├── data/
│   ├── policy/                   # source policy docs (md/pdf/csv/xlsx)
│   ├── synthetic_requests/       # generated test PA requests
│   └── cache/                    # ingestion hash cache
├── scripts/
│   ├── ingest_policy_pinecone.py # adapted from cold-chain ingestion pattern
│   ├── setup_db_roles.sql        # DB-level guardrail enforcement
│   └── generate_synthetic_data.py
├── src/
│   ├── orchestrator.py           # LangGraph state graph
│   ├── agents/
│   │   ├── intake_agent.py
│   │   ├── policy_retrieval.py
│   │   └── decision_agent.py
│   ├── tools/
│   │   ├── db_tools.py           # read-only member/plan/request queries
│   │   └── policy_tools.py       # Pinecone retrieval wrapper
│   ├── schemas.py                # Pydantic models — requests, decisions, state
│   ├── audit.py                  # append-only audit log writer/reader
│   ├── config.py                 # model gateway + escalation threshold config
│   └── ui.py                     # Streamlit app
└── eval/
    ├── test_cases.jsonl          # held-out requests with known correct outcomes
    └── run_eval.py
```

## 2. Database Schema (Postgres)

```sql
-- Member / plan / provider — synthetic, no real PHI
CREATE TABLE members (
    member_id       UUID PRIMARY KEY,
    plan_id         UUID NOT NULL REFERENCES plans(plan_id),
    date_of_birth   DATE NOT NULL,
    gender          VARCHAR(20)
);

CREATE TABLE plans (
    plan_id         UUID PRIMARY KEY,
    plan_name       VARCHAR(200) NOT NULL,
    plan_year       INT NOT NULL
);

CREATE TABLE providers (
    provider_id     UUID PRIMARY KEY,
    npi             VARCHAR(20) NOT NULL,
    provider_name   VARCHAR(200) NOT NULL
);

-- PA request lifecycle
CREATE TABLE pa_requests (
    request_id          UUID PRIMARY KEY,
    member_id           UUID NOT NULL REFERENCES members(member_id),
    provider_id          UUID NOT NULL REFERENCES providers(provider_id),
    service_code         VARCHAR(20) NOT NULL,      -- CPT/HCPCS/NDC
    service_description   TEXT,
    submitted_at          TIMESTAMPTZ NOT NULL,
    status                VARCHAR(20) NOT NULL,       -- submitted / pending_docs / decided
    request_type          VARCHAR(20) NOT NULL         -- standard / expedited
);

CREATE TABLE pa_request_documents (
    document_id     UUID PRIMARY KEY,
    request_id      UUID NOT NULL REFERENCES pa_requests(request_id),
    doc_type        VARCHAR(50) NOT NULL,   -- clinical_note, imaging, lab_result, etc.
    content_ref     TEXT NOT NULL           -- path/ref to synthetic doc content
);

CREATE TABLE pa_decisions (
    decision_id      UUID PRIMARY KEY,
    request_id        UUID NOT NULL REFERENCES pa_requests(request_id),
    outcome            VARCHAR(20) NOT NULL,     -- approve / deny / escalate
    citation            TEXT,                      -- required if outcome = deny
    confidence          FLOAT NOT NULL,
    decided_at          TIMESTAMPTZ NOT NULL,
    decided_by           VARCHAR(20) NOT NULL     -- 'agent' or reviewer_id
);

-- Append-only. No UPDATE/DELETE grants at all (see setup_db_roles.sql)
CREATE TABLE audit_log (
    log_id          BIGSERIAL PRIMARY KEY,
    request_id      UUID NOT NULL,
    step            VARCHAR(50) NOT NULL,   -- intake / policy_retrieval / decision / escalation
    detail          JSONB NOT NULL,          -- tool calls, retrieved chunks, reasoning summary
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Guardrail enforcement (`setup_db_roles.sql`)**, same pattern as cold-chain:

```sql
CREATE ROLE pa_agent_role LOGIN PASSWORD '...';
GRANT SELECT ON members, plans, providers, pa_requests, pa_request_documents TO pa_agent_role;
GRANT SELECT, INSERT ON pa_decisions TO pa_agent_role;   -- can record, not alter/delete
GRANT INSERT ON audit_log TO pa_agent_role;              -- append-only
REVOKE UPDATE, DELETE ON pa_decisions, audit_log FROM pa_agent_role;
REVOKE ALL ON members, plans, providers FROM pa_agent_role EXCEPT SELECT;
```

## 3. Pydantic Schemas (`src/schemas.py`)

```python
from pydantic import BaseModel, Field
from typing import Literal, Optional
from datetime import date, datetime

class PARequest(BaseModel):
    request_id: str
    member_id: str
    provider_id: str
    service_code: str
    service_description: str
    request_type: Literal["standard", "expedited"]
    documents: list[str] = []          # doc_type values present

class PolicyChunk(BaseModel):
    text: str
    source_file: str
    section_header: Optional[str] = None
    effective_date: Optional[date] = None

class Decision(BaseModel):
    outcome: Literal["approve", "deny", "escalate"]
    citation: Optional[str] = None      # required if outcome == "deny"
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_summary: str

    def validate_citation_rule(self) -> bool:
        # Hard constraint from HLD 3.3 — a deny without a citation is invalid output
        if self.outcome == "deny" and not self.citation:
            return False
        return True

class GraphState(BaseModel):
    request: PARequest
    missing_documents: list[str] = []
    retrieved_policy: list[PolicyChunk] = []
    decision: Optional[Decision] = None
    status: Literal["intake", "pending_docs", "policy_retrieval", "decided"] = "intake"
```

## 4. LangGraph Orchestration (`src/orchestrator.py`)

**Nodes** (each a plain function taking/returning `GraphState`):

```python
def intake_node(state: GraphState) -> GraphState: ...
    # checks state.request.documents against a required-docs checklist
    # per service_code; sets state.missing_documents and state.status

def policy_retrieval_node(state: GraphState) -> GraphState: ...
    # calls policy_tools.retrieve(service_code, request_date)
    # filters by effective_date <= request.submitted_at <= superseded_date/None
    # sets state.retrieved_policy

def decision_node(state: GraphState) -> GraphState: ...
    # LLM call with request + retrieved_policy -> Decision
    # validates via Decision.validate_citation_rule(); re-prompts once if invalid,
    # then hard-fails to escalate if still invalid (never silently drops the rule)

def escalation_node(state: GraphState) -> GraphState: ...
    # writes to human reviewer queue with reasoning_summary attached
```

**Edges / routing**:

```python
graph.add_conditional_edges(
    "intake",
    lambda s: "policy_retrieval" if not s.missing_documents else "END_pending_docs"
)
graph.add_edge("policy_retrieval", "decision")
graph.add_conditional_edges(
    "decision",
    lambda s: "escalation" if s.decision.outcome == "escalate"
              or s.decision.confidence < config.ESCALATION_THRESHOLD
              else "END_decided"
)
```

Every node writes one `audit_log` row before returning (tool calls made, chunks
retrieved, decision produced) — audit writing is not a separate pass over the
state at the end, it happens inline so a crash mid-graph still leaves a trail.

## 5. Escalation Threshold

- Starting value: `ESCALATION_THRESHOLD = 0.75` (config, not hardcoded in a prompt)
- Confidence is produced by the Decision Agent itself as part of its structured
  output, not computed post-hoc — the prompt explicitly instructs it to rate its
  own certainty against how directly the retrieved policy addresses the specific
  request
- This value is exactly what the eval harness (Section 7) will be used to tune —
  ship with 0.75, then adjust based on false-escalation rate from eval results

## 6. Tool Signatures (`src/tools/`)

```python
# db_tools.py
def get_member_plan(member_id: str) -> dict: ...
def get_request_documents(request_id: str) -> list[dict]: ...
def get_prior_requests_for_member(member_id: str) -> list[dict]:
    # not used by Decision Agent in Phase 1 — reserved for the
    # Fraud/Abuse agent extension discussed separately

# policy_tools.py
def retrieve_policy(service_code: str, request_date: date, k: int = 5) -> list[PolicyChunk]: ...
```

Both modules only ever open the DB/Pinecone connection with the restricted
`pa_agent_role` credentials — never an admin connection string, even in dev.

## 7. Eval Harness (`eval/`)

- `test_cases.jsonl`: ~30–50 synthetic requests spanning clean-approve,
  clean-deny, missing-docs, and genuinely-ambiguous cases, each with an expected
  outcome and (for denies) expected citation section
- `run_eval.py` runs each case through the full graph and scores:
  - **Decision accuracy**: predicted outcome == expected outcome
  - **Citation correctness**: for denies, does the cited section match expected
  - **False-escalation rate**: cases labeled clean-approve/deny that the agent
    escalated instead
- Output: a simple scored table, re-run after any prompt or threshold change

## 8. Prompt Structure (Decision Agent)

Fixed sections, not free-form:
1. Role/task framing (decide PA requests strictly from provided policy text)
2. The request details (service, member plan, submitted documentation)
3. The retrieved policy chunks, each tagged with its source/section
4. Explicit instruction: never approve/deny based on anything not present in the
   retrieved chunks; if the chunks don't clearly resolve it, escalate
5. Required output format matching the `Decision` schema exactly

## 9. What's Deliberately Deferred Here

- Coverage/Clinical-Necessity agent split and Fraud/Abuse agent — next pass, per
  the earlier discussion, once this linear version runs end-to-end
- `get_prior_requests_for_member` tool exists in the schema already so the
  Fraud/Abuse agent addition doesn't require a data-model change later, only new
  orchestration
- Model gateway config and Docker/cloud deployment details — carried over
  directly from the cold-chain project's existing pattern, not re-specified here
