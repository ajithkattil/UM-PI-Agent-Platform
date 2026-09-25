# Prior Authorization Agent — Use Case Document

**Project**: Agentic AI Payer Platform — Phase 1 (Prior Authorization)
**Status**: Draft for build
**Author**: Ajith

---

## 1. Background & Problem Statement

Prior authorization (PA) is the process by which a provider must obtain a payer's
approval before delivering certain services, procedures, or prescriptions. Today
this process is largely manual: intake staff and clinical reviewers manually check
a request against coverage policy, often taking days and producing inconsistent
decisions across similar cases.

CMS's Interoperability and Prior Authorization Final Rule (CMS-0057-F) now makes
this a compliance-critical problem, not just an efficiency one:

- Payers must respond to **standard PA requests within 7 calendar days** and
  **expedited requests within 72 hours** (effective Jan 1, 2026 — already in force).
- Payers must provide a **specific, citable reason for any denial**.
- Payers must expose PA data through **FHIR APIs** (deadline Jan 1, 2027).
- Payers must **publicly report PA turnaround metrics** annually.

Manual review at current staffing levels cannot reliably hit these deadlines at
scale. This creates the business case for an agentic system that handles the
majority of requests autonomously, with a human reviewer only for genuine
judgment calls.

## 2. Goal

Build an agentic AI system that ingests a PA request, determines the applicable
coverage policy, evaluates the request against that policy, and returns one of:
**approve**, **deny with a specific cited reason**, or **escalate to a human
reviewer** — within the regulatory turnaround window, with a full audit trail.

## 3. Actors

| Actor | Role |
|---|---|
| Provider / provider staff | Submits the PA request, may be asked for additional documentation |
| Payer's medical director / clinical reviewer | Handles escalated, borderline, or low-confidence cases |
| Compliance / audit team | Reviews decision logs to confirm regulatory adherence |
| The agent system | Classifies, retrieves policy, evaluates, decides, explains, escalates |

## 4. In Scope (Phase 1)

- Single-service PA requests (one procedure/drug/service per request)
- Coverage policy retrieval and citation (RAG over policy documents)
- Documentation-completeness check (is the submission missing required info)
- Approve / deny-with-reason / escalate decisioning
- Full audit logging of every decision and the policy it was based on
- Human-in-the-loop escalation path for low-confidence or high-risk cases
- Turnaround-time tracking against the 72hr/7-day thresholds

## 5. Out of Scope (Phase 1 — reserved for later phases)

- Claims authorization (Phase 2 — will reuse this platform's policy RAG, decision
  engine, guardrails, and audit layer)
- Multi-service / bundled requests
- Live FHIR API exposure (API compliance deadline is Jan 2027 — architecture should
  not preclude this, but it isn't being built now)
- Direct integration with a real payer's production systems — this is a POC against
  synthetic/representative data

## 6. Core User Scenarios

**Scenario A — Clean approval**
Provider submits a request for a service that clearly meets documented coverage
criteria. Agent retrieves the relevant policy, confirms all required documentation
is present, approves, and logs the decision with the policy citation. No human
involved.

**Scenario B — Missing documentation**
Provider submits a request missing a required clinical note. Agent identifies the
gap, responds asking specifically for the missing item (not a generic rejection),
and re-evaluates once resubmitted.

**Scenario C — Clear denial**
Request does not meet documented medical necessity criteria for the member's plan.
Agent denies with a specific, cited reason ("does not meet criterion X of policy Y").

**Scenario D — Escalation**
Request is borderline — meets some but not all criteria, or the policy language is
ambiguous relative to the clinical details provided. Agent does **not** guess; it
routes to a human reviewer with a summary of what it found and why it's unsure.

## 7. Success Metrics

| Metric | Target |
|---|---|
| Turnaround time (auto-decided cases) | Well under the 72hr/7-day regulatory ceiling |
| Auto-decision rate | Majority of clean-cut cases resolved without human review |
| Denial-reason citation accuracy | Every denial traceable to a specific policy clause |
| Escalation precision | Escalated cases are genuinely borderline, not false triggers from an overly cautious agent |
| Audit completeness | 100% of decisions logged with policy citation and reasoning trace |

## 8. Non-Functional Requirements

- **Guardrails**: agent has read-only access to policy and member data; cannot
  write/modify coverage records or auto-finalize a denial without a citable reason
- **Compliance**: every denial must include a specific reason, per CMS-0057-F
- **Auditability**: decision + reasoning + policy citation logged and queryable
- **Multi-turn**: system must support a request → clarification → resubmission loop
- **Model flexibility**: reasoning engine should not be hard-coupled to one LLM provider

## 9. Assumptions

- Coverage policy documents and synthetic PA request data will be used in place of
  real payer data (no PHI/production access for the POC)
- Phase 1 targets a single hypothetical payer's policy set, not a generic
  plan-agnostic engine (decision made for build speed; can be revisited later)

## 10. What Phase 2 (Claims) Will Reuse From This Platform

- Policy/coverage RAG layer (extended with billing/medical-necessity policies)
- Decision engine pattern (approve / deny-with-reason / escalate)
- Guardrail and audit logging architecture
- Model gateway and eval harness
