# Claims / Payment Integrity Agent — Low-Level Design (LLD)

**Project**: Agentic AI Payer Platform — Phase 2 (Claims / Payment Integrity)
**Status**: Draft — implements the confirmed design in
`docs/05-Claims-High-Level-Design.md`, with the four scoping decisions locked
in: deterministic reconciliation, rule-computed fraud signals, duplicate
billing as an intake-level check, and two separate thresholds

---

## 1. Repository Layout (additions to the existing repo)

```
data/
├── policy/                        # unchanged — same 4 policies, reused
├── claims/                        # NEW
│   └── synthetic_claims/          # hand-authored claim scenarios
scripts/
├── setup_claims_db_roles.sql      # NEW — claims_agent_role, extends pa_admin_role
├── schema_claims.sql              # NEW — claims, claim_documents, claim_decisions
├── generate_synthetic_claims.py   # NEW
src/
├── schemas.py                     # extended — ClaimRequest, ClaimDecision, etc.
├── config.py                      # extended — CLAIMS_ESCALATION_THRESHOLD, SIU_FLAG_THRESHOLD
├── agents/
│   ├── claim_intake_agent.py      # NEW — completeness + duplicate check (no LLM)
│   ├── coverage_agent.py          # NEW — Phase 1 Decision Agent's direct descendant
│   ├── pa_xref_agent.py           # NEW — no LLM call, pure DB lookup + comparison
│   ├── fraud_agent.py             # NEW — reasons over precomputed signals
│   └── reconciliation.py          # NEW — deterministic priority rules, no LLM
├── tools/
│   ├── claims_db_tools.py         # NEW
│   └── fraud_signals.py           # NEW — rule-based signal computation (no LLM)
├── claims_orchestrator.py         # NEW — supervisor/dispatch graph
eval/
├── claims_test_cases.jsonl        # NEW
├── run_claims_eval.py             # NEW
```

## 2. Database Schema (additions)

```sql
CREATE TABLE claims (
    claim_id            UUID PRIMARY KEY,
    member_id            UUID NOT NULL REFERENCES members(member_id),
    provider_id           UUID NOT NULL REFERENCES providers(provider_id),
    service_code           VARCHAR(20) NOT NULL,
    service_description     TEXT,
    billed_amount             NUMERIC(10,2) NOT NULL,
    date_of_service            DATE NOT NULL,
    submitted_at                TIMESTAMPTZ NOT NULL,
    status                       VARCHAR(20) NOT NULL,   -- submitted / pending_docs / decided
    clinical_notes                TEXT
);

CREATE TABLE claim_documents (
    document_id     UUID PRIMARY KEY,
    claim_id        UUID NOT NULL REFERENCES claims(claim_id),
    doc_type        VARCHAR(50) NOT NULL,
    content_ref     TEXT NOT NULL
);

-- Records EACH specialist's signal individually, not just the reconciled
-- outcome — an SIU investigator or auditor needs to see which agent flagged
-- what and at what confidence, not just the final verdict (confirmed design
-- decision, HLD Section 4.5 / this doc's recommendation).
CREATE TABLE claim_decisions (
    decision_id         UUID PRIMARY KEY,
    claim_id             UUID NOT NULL REFERENCES claims(claim_id),
    outcome               VARCHAR(20) NOT NULL,    -- pay / deny / flag_siu / escalate
    citation                TEXT,                     -- required if outcome = deny
    coverage_signal          JSONB NOT NULL,   -- {outcome, citation, confidence, reasoning_summary}
    pa_xref_signal            JSONB NOT NULL,   -- {pa_found, pa_outcome, matches_billed_service}
    fraud_signal                JSONB NOT NULL,   -- {flagged, confidence, reasoning_summary, computed_metrics}
    confidence                    FLOAT NOT NULL,   -- the reconciled decision's overall confidence
    decided_at                     TIMESTAMPTZ NOT NULL,
    decided_by                      VARCHAR(20) NOT NULL   -- 'agent' or reviewer_id
);
```

**Guardrail roles (`setup_claims_db_roles.sql`)** — new role, same pattern as
Phase 1, not a shortcut through it:

```sql
CREATE ROLE claims_agent_role LOGIN PASSWORD 'change_me_in_env';

-- Read-only on claims data AND on Phase 1's pa_decisions — this is the
-- literal DB-level expression of "Phase 2 reads Phase 1's output" from the
-- combined architecture diagram.
GRANT SELECT ON claims, claim_documents, members, plans, providers, pa_decisions
    TO claims_agent_role;

-- Can record its own decisions and audit entries, same append-only pattern
-- as pa_agent_role. Cannot write to claims/claim_documents at all — the
-- role that submits a claim is not the role that decides it, mirroring
-- pa_intake_role vs pa_agent_role.
GRANT SELECT, INSERT ON claim_decisions TO claims_agent_role;
GRANT INSERT ON audit_log TO claims_agent_role;
REVOKE UPDATE, DELETE ON claim_decisions FROM claims_agent_role;
REVOKE INSERT, UPDATE, DELETE ON claims, claim_documents, pa_decisions
    FROM claims_agent_role;
GRANT CONNECT ON DATABASE pa_agent_poc TO claims_agent_role;

-- Claims intake role — submission only, mirrors pa_intake_role exactly.
CREATE ROLE claims_intake_role LOGIN PASSWORD 'change_me_in_env';
GRANT SELECT, INSERT ON claims, claim_documents TO claims_intake_role;
GRANT SELECT ON members, plans, providers, claim_decisions, pa_decisions
    TO claims_intake_role;
REVOKE UPDATE, DELETE ON claims, claim_documents FROM claims_intake_role;
GRANT CONNECT ON DATABASE pa_agent_poc TO claims_intake_role;

-- pa_admin_role is EXTENDED, not duplicated — it's already the audit/reviewer
-- role for Phase 1; Phase 2's SIU and claims-examiner queues are the same
-- kind of human-reviewer function, just a second queue on the same role.
GRANT SELECT ON claims, claim_documents, claim_decisions TO pa_admin_role;
GRANT INSERT ON claim_decisions TO pa_admin_role;
```

## 3. Pydantic Schemas (additions to `src/schemas.py`)

```python
class ClaimRequest(BaseModel):
    claim_id: str
    member_id: str
    provider_id: str
    service_code: str
    service_description: str
    billed_amount: float
    date_of_service: date
    documents: list[str] = []
    clinical_notes: str = ""

class SpecialistSignal(BaseModel):
    """Common shape all three specialist agents produce — the supervisor's
    reconciliation logic (Section 5) consumes these, never raw agent text."""
    agent_name: Literal["coverage", "pa_xref", "fraud"]
    flagged_outcome: str          # agent-specific: approve/deny for coverage,
                                   # found/missing for pa_xref, flagged/clear for fraud
    citation: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_summary: str
    computed_metrics: dict = {}   # fraud agent populates this; others leave empty

class ClaimDecision(BaseModel):
    outcome: Literal["pay", "deny", "flag_siu", "escalate"]
    citation: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    coverage_signal: SpecialistSignal
    pa_xref_signal: SpecialistSignal
    fraud_signal: SpecialistSignal

    def validate_citation_rule(self) -> bool:
        # Same hard constraint as Phase 1: a deny without a citation is
        # invalid output.
        if self.outcome == "deny" and not self.citation:
            return False
        return True

class ClaimGraphState(BaseModel):
    claim: ClaimRequest
    missing_documents: list[str] = []
    is_duplicate: bool = False
    duplicate_of_claim_id: Optional[str] = None
    retrieved_policy: list[PolicyChunk] = []
    coverage_signal: Optional[SpecialistSignal] = None
    pa_xref_signal: Optional[SpecialistSignal] = None
    fraud_signal: Optional[SpecialistSignal] = None
    decision: Optional[ClaimDecision] = None
    status: Literal["intake", "pending_docs", "dispatched", "decided"] = "intake"
```

## 4. Config additions (`src/config.py`)

```python
CLAIMS_ESCALATION_THRESHOLD = float(os.getenv("CLAIMS_ESCALATION_THRESHOLD", "0.75"))
SIU_FLAG_THRESHOLD = float(os.getenv("SIU_FLAG_THRESHOLD", "0.85"))
# Deliberately higher than the escalation threshold — a false SIU flag has
# real provider-relations cost beyond a wasted review cycle (HLD Section 7),
# so the bar to actually flag someone is set more conservatively than the
# bar to merely ask a human to look at something.

DUPLICATE_CLAIM_WINDOW_DAYS = int(os.getenv("DUPLICATE_CLAIM_WINDOW_DAYS", "14"))
FRAUD_LOOKBACK_WINDOW_DAYS = int(os.getenv("FRAUD_LOOKBACK_WINDOW_DAYS", "90"))
FRAUD_VOLUME_THRESHOLD_MULTIPLIER = float(os.getenv("FRAUD_VOLUME_THRESHOLD_MULTIPLIER", "3.0"))
# A provider billing more than this multiple of the per-provider median
# volume for a service, within the lookback window, is a computed signal fed
# to the Fraud Agent — see fraud_signals.py. The agent reasons over this
# number; it does not compute it itself.
```

## 5. Orchestration (`src/claims_orchestrator.py`)

**Nodes**:

```python
def claim_intake_node(state: ClaimGraphState) -> ClaimGraphState: ...
    # 1. Documentation completeness check (same pattern as Phase 1 intake)
    # 2. Duplicate check — deterministic, NOT the Fraud Agent's job (confirmed
    #    scoping decision): same member + service_code + date_of_service
    #    within DUPLICATE_CLAIM_WINDOW_DAYS of an existing claim -> is_duplicate=True
    # Sets state.status accordingly

def coverage_node(state: ClaimGraphState) -> ClaimGraphState: ...
    # Retrieves policy (same Pinecone corpus as Phase 1), LLM call structured
    # identically to Phase 1's Decision Agent, including the citation
    # validation/retry logic — this is a near-verbatim reuse of
    # decision_agent.py's pattern, not a rewrite.

def pa_xref_node(state: ClaimGraphState) -> ClaimGraphState: ...
    # NO LLM call — pure DB lookup + comparison logic:
    #   - does this service_code require a PA (same REQUIRED-PA services as
    #     Phase 1's 4 policies)?
    #   - if yes, query pa_decisions for a matching member+service_code
    #   - compare billed_amount / date_of_service plausibility against the
    #     approved PA, if found
    # Deterministic because "does a matching approved PA exist" is a fact
    # lookup, not a judgment call — no reason to spend an LLM call on it.

def fraud_node(state: ClaimGraphState) -> ClaimGraphState: ...
    # 1. Calls fraud_signals.compute_signals(provider_id, service_code) —
    #    rule-based, returns real numbers (claim count in window vs. median,
    #    threshold-clustering flag)
    # 2. LLM call: reasons over those PRECOMPUTED numbers into a narrative
    #    signal — the agent explains/contextualizes a number it did not
    #    invent, it never free-associates a pattern from raw history.

def reconcile_node(state: ClaimGraphState) -> ClaimGraphState: ...
    # Deterministic priority rules (Section 6) — no LLM call. Combines the
    # three SpecialistSignals into one ClaimDecision, writes to
    # claim_decisions and audit_log.
```

**Edges**:

```python
graph.add_conditional_edges("claim_intake", route_after_intake, {
    "dispatch": "dispatch_specialists",   # fan-out to all three, parallel
    END: END,                              # pending_docs short-circuit
})
# All three specialists run off the same post-intake state; none depends on
# another's output (HLD Section 6) — modeled as three parallel edges into a
# join before reconcile, not a sequential chain.
graph.add_edge("dispatch_specialists", "coverage")
graph.add_edge("dispatch_specialists", "pa_xref")
graph.add_edge("dispatch_specialists", "fraud")
graph.add_edge(["coverage", "pa_xref", "fraud"], "reconcile")
graph.add_edge("reconcile", END)
```

## 6. Reconciliation Logic (`src/agents/reconciliation.py`) — deterministic, confirmed

```python
def reconcile(coverage: SpecialistSignal, pa_xref: SpecialistSignal,
              fraud: SpecialistSignal) -> ClaimDecision:
    # Priority order, fixed in code — not an LLM judgment call, so it's both
    # auditable and independently unit-testable with scripted fake signals
    # (same approach as test_orchestrator_routing.py in Phase 1).

    if fraud.flagged_outcome == "flagged" and fraud.confidence >= config.SIU_FLAG_THRESHOLD:
        return ClaimDecision(outcome="flag_siu", confidence=fraud.confidence,
                              citation=fraud.reasoning_summary, ...)

    if pa_xref.flagged_outcome == "missing_or_denied":
        return ClaimDecision(outcome="deny", citation=pa_xref.citation,
                              confidence=pa_xref.confidence, ...)

    if coverage.flagged_outcome == "deny":
        return ClaimDecision(outcome="deny", citation=coverage.citation,
                              confidence=coverage.confidence, ...)

    lowest_confidence = min(coverage.confidence, pa_xref.confidence, fraud.confidence)
    if lowest_confidence < config.CLAIMS_ESCALATION_THRESHOLD:
        return ClaimDecision(outcome="escalate", confidence=lowest_confidence, ...)

    return ClaimDecision(outcome="pay", confidence=lowest_confidence, ...)
```

## 7. Fraud Signal Computation (`src/tools/fraud_signals.py`) — rule-based, no LLM

```python
def compute_signals(provider_id: str, service_code: str) -> dict:
    # Real SQL against claims history within FRAUD_LOOKBACK_WINDOW_DAYS:
    #   - this_provider_claim_count for (provider_id, service_code)
    #   - median_claim_count across all providers for the same service_code
    #     (the peer-comparison baseline that makes "3x median" meaningful
    #     rather than an arbitrary absolute number)
    #   - threshold_clustering: count of claims within 5% of any known
    #     review-threshold dollar amount for this service
    # Returns a plain dict of numbers — no interpretation, no LLM call here.
    ...
```

The Fraud Agent's prompt (mirroring Phase 1's fixed-section structure)
receives these numbers directly and is instructed to reason ONLY from them —
same "decide strictly from what's provided, escalate rather than guess"
discipline as Phase 1's Decision Agent, applied to numbers instead of policy
text.

## 8. Tool Signatures (`src/tools/claims_db_tools.py`)

```python
def get_claim(claim_id: str) -> dict: ...
def get_claim_documents(claim_id: str) -> list[str]: ...
def check_duplicate_claim(member_id: str, service_code: str, date_of_service: date) -> str | None:
    # Returns the existing claim_id if a duplicate is found within
    # DUPLICATE_CLAIM_WINDOW_DAYS, else None. Deterministic, used by
    # claim_intake_node — not the Fraud Agent (confirmed scoping decision).
def get_pa_decision_for_service(member_id: str, service_code: str) -> dict | None:
    # Reads pa_decisions (Phase 1's table) — the literal DB-level expression
    # of the cross-phase dependency in the combined architecture diagram.
def record_claim_decision(claim_id: str, decision: ClaimDecision, decided_by: str) -> str: ...
```

## 9. Eval Harness (`eval/claims_test_cases.jsonl` + `run_claims_eval.py`)

Same hand-authored-not-random discipline as Phase 1's 14 scenarios, covering:
- Clean pay (PA on file, matches, no anomaly)
- Deny — no matching PA where one was required
- Deny — coverage/medical necessity not met (independent of PA)
- Duplicate — caught at intake, never reaches the specialists
- Flag SIU — a case with a genuinely planted volume anomaly or
  threshold-clustering pattern in the synthetic claims history (not a vague
  "looks suspicious" case — must be traceable to a specific computed metric)
- Escalate — genuinely ambiguous single-claim case, distinct from an SIU flag

Scoring mirrors Phase 1's `run_eval.py`: decision accuracy, citation
correctness on denies, false-escalation rate — plus a **false-SIU-flag
rate** as its own tracked metric, given the asymmetric cost the HLD calls
out.

## 10. What's Deliberately Deferred

- Appeals workflow for denied/flagged claims (Phase 3 concern, same as PA's
  appeals agent extension)
- Real EDI claim formats — synthetic data only, same as Phase 1
- Multi-line/bundled claims — one claim, one service, same simplification
  Phase 1 made
