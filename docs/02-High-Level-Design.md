# Prior Authorization Agent — High-Level Design (HLD)

**Project**: Agentic AI Payer Platform — Phase 1 (Prior Authorization)
**Status**: Draft
**Scope**: Single hypothetical payer, synthetic data, no PHI

---

## 1. Design Goals (from the Use Case Document)

- Auto-decide the majority of clean-cut PA requests within the regulatory window
- Every denial carries a specific, citable policy reason
- Every decision — auto or human — is logged and queryable
- Borderline cases escalate to a human reviewer instead of being guessed
- Reasoning engine is not locked to a single model provider

## 2. System Overview

The system is a request-driven pipeline, not a persistent conversation by default:
a PA request arrives, the agent reasons over it using retrieved policy and
structured member/plan data, and produces a decision. Multi-turn behavior exists
specifically for the "missing documentation" loop (Scenario B) — the same request
thread stays open across a clarification round-trip.

```
                    ┌─────────────────────────────────────────┐
                    │              Streamlit UI                │
                    │   (submit request / view decision /      │
                    │    reviewer queue for escalations)        │
                    └───────────────────┬───────────────────────┘
                                        │
                    ┌───────────────────▼───────────────────────┐
                    │            Orchestrator (LangGraph)        │
                    │                                             │
                    │   ┌─────────────┐   ┌───────────────────┐  │
                    │   │  Intake /   │──▶│  Policy Retrieval  │  │
                    │   │  Completeness│   │  (RAG over policy  │  │
                    │   │  Agent       │   │  docs, Pinecone)   │  │
                    │   └─────────────┘   └─────────┬──────────┘  │
                    │                                 │            │
                    │                     ┌───────────▼─────────┐  │
                    │                     │   Decision Agent     │  │
                    │                     │ approve / deny+cite / │  │
                    │                     │   escalate            │  │
                    │                     └───────────┬──────────┘  │
                    └─────────────────────────────────┼─────────────┘
                                                        │
                ┌───────────────────────────────────────┼───────────────────────┐
                │                                        │                       │
    ┌───────────▼──────────┐               ┌─────────────▼───────────┐  ┌────────▼────────┐
    │  Member/Plan DB       │               │   Audit Log (append-    │  │  Human Reviewer  │
    │  (Postgres, read-only │               │   only, queryable)      │  │  Queue           │
    │  agent role)          │               │                         │  │  (escalations)   │
    └───────────────────────┘               └─────────────────────────┘  └──────────────────┘
```

## 3. Components

### 3.1 Intake / Completeness Agent
- Validates the incoming request structure (member ID, requested service/code,
  requesting provider, attached clinical documentation)
- Checks against a documentation checklist for the requested service type
- If incomplete: generates a specific "missing X" response (Scenario B) instead of
  passing an incomplete request downstream
- If complete: passes to Policy Retrieval

### 3.2 Policy Retrieval (RAG)
- Retrieves the relevant coverage policy section(s) for the requested service,
  using the header-aware chunking + metadata-tagged ingestion pipeline adapted
  from the cold-chain project (Section_Header, source_file, effective_date)
- Returns retrieved chunks **with citations**, not a synthesized answer — citation
  fidelity matters more here than fluency
- Filters retrieval by policy `effective_date` against the request date (the
  effective-dating gap identified during the ingestion review)

### 3.3 Decision Agent
- Takes the request details + retrieved policy chunks
- Evaluates fit against criteria and produces one of three outcomes:
  - **Approve** — criteria clearly met
  - **Deny** — criteria clearly not met, with the specific clause cited
  - **Escalate** — ambiguous fit, conflicting policy language, or confidence below
    threshold
- Never allowed to deny without a citation attached (hard constraint on the
  output schema, not just prompt instruction)

### 3.4 Guardrails
- Agent's DB role is **read-only** against member/plan/policy tables — same
  DB-role-enforcement pattern as cold-chain (`DENY INSERT/UPDATE/DELETE` at the
  schema level, not just application-layer checks)
- Decision Agent output is schema-validated before being accepted — a denial
  without a citation field populated is rejected and re-run, not passed through
- Escalation threshold is a config value, not a per-call LLM judgment call, so it
  can be tuned without touching prompts

### 3.5 Audit Log
- Append-only table capturing: request ID, timestamp, all tool calls made, policy
  chunks retrieved, decision, citation, confidence signal, and (if escalated) the
  reviewer's eventual outcome
- Queryable via the same admin-gated log viewer pattern from cold-chain
- This is the artifact that answers "prove this decision was compliant" —
  designed for that purpose specifically, not as a debugging afterthought

### 3.6 Human Reviewer Queue
- Escalated cases land here with the agent's partial reasoning attached (what it
  found, why it wasn't confident) so the reviewer isn't starting cold
- Reviewer decision is captured back into the audit log, closing the loop

### 3.7 Model Gateway
- Config-driven switch across providers (OpenAI / Anthropic / local fallback),
  same `.env`-based pattern as cold-chain
- Enables a real cost/latency comparison talking point and avoids hard-coupling
  the decision engine to one vendor

### 3.8 Eval Harness
- A held-out set of synthetic requests with known correct outcomes (approve/deny/
  escalate + expected citation)
- Scored for: decision accuracy, citation correctness, and false-escalation rate
  (escalating something that was actually clear-cut wastes the reviewer's time and
  undercuts the auto-decision-rate metric from the use case doc)

## 4. Data Stores

| Store | Purpose | Access pattern |
|---|---|---|
| Postgres | Member, plan, provider, request records | Agent: read-only. UI: read/write for new requests |
| Pinecone | Policy document chunks + metadata | Agent: read-only (query only, no write path from the agent) |
| Audit log table (Postgres) | Decision trail | Agent: append-only. Compliance/admin: read |

## 5. Orchestration Pattern

LangGraph state graph: `intake → [complete?] → policy_retrieval → decision → [approve/deny/escalate]`,
with the incomplete-documentation path looping back to intake after resubmission.
This is a simpler graph than the cold-chain ReAct loop by design — PA decisioning
is closer to a structured pipeline with one clear branch point (escalation) than an
open-ended tool-calling loop, and forcing it into a heavier multi-agent pattern
prematurely would add complexity without matching the actual decision structure.

## 6. Non-Functional Design Notes

- **No PHI**: all member/request data is synthetic for this POC; schema is designed
  to look like a real payer's data model without containing real identifiers
- **Turnaround tracking**: every request records intake timestamp and decision
  timestamp, so the 72hr/7-day compliance metric is measurable directly from the
  audit log, not estimated after the fact
- **Extensibility for Phase 2 (Claims)**: Policy Retrieval, the Decision Agent
  pattern, the guardrail/audit layer, and the model gateway are all built as
  reusable to avoid this being a PA-only system — see Use Case Document Section 10

## 7. Deployment (POC scope)

Local development first (Docker Compose: Postgres + app), matching the cold-chain
project's environment setup rather than provisioning cloud infra before the core
logic is proven. Cloud deployment (EC2/ECS) is a later step once the design is
validated end-to-end locally.

## 8. Open Questions for LLD

- Exact schema for the member/plan/request/audit tables
- Exact tool/function signatures for the agent (what the Decision Agent is
  literally allowed to call)
- Escalation confidence threshold — starting value and how it's derived
- Synthetic data generation approach for policy docs + test requests
