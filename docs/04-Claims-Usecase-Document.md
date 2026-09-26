# Claims / Payment Integrity Agent — Use Case Document

**Project**: Agentic AI Payer Platform — Phase 2 (Claims / Payment Integrity)
**Status**: Draft for review — no HLD/LLD/code yet
**Depends on**: Phase 1 (Prior Authorization) — see `docs/01-Usecase-Document.md`
Section 10 for what this phase was always meant to reuse

---

## 1. Background & Problem Statement

Prior authorization (Phase 1) is a **pre-service** check: does this request meet
coverage criteria before the service happens. Claims / Payment Integrity is the
**post-service** counterpart: a provider has already delivered a service and
billed for it, and the payer must decide whether to pay, deny, or flag the
claim before money moves.

This is a different moment in the same lifecycle, checked against much of the
same policy knowledge — which is exactly why this phase reuses so much of
Phase 1 rather than being a separate system. But it introduces a genuinely new
kind of risk PA doesn't have: a provider claim can be individually plausible
but part of a fraudulent, wasteful, or abusive **pattern** only visible across
many claims — something a single-request evaluator (which is all Phase 1 is)
structurally cannot see.

Payment Integrity failures are expensive and heavily scrutinized: incorrectly
paid claims, duplicate billing, and undetected fraud/waste/abuse (FWA) expose a
payer to direct financial loss and regulatory/compliance risk. This is the
domain your real Neural Labs payer-side work maps to most directly.

## 2. Goal

Build an agentic system that ingests a submitted claim, determines whether it
meets medical necessity and billing-accuracy criteria (reusing the same policy
corpus as Phase 1), cross-references it against any prior authorization on
file, checks it against the billing patterns of the same provider/member over
time, and returns **pay**, **deny with a specific cited reason**, or **flag for
SIU (Special Investigations Unit) review** — with the same audit/guardrail
discipline Phase 1 already proved out.

## 3. Actors

| Actor | Role |
|---|---|
| Provider / billing staff | Submits the claim for payment |
| SIU investigator | Reviews claims flagged for suspected fraud/waste/abuse |
| Payer's claims examiner | Handles escalated, borderline, or low-confidence cases (the claims-side equivalent of Phase 1's medical director) |
| Compliance / audit team | Reviews decision logs, same as Phase 1 |
| The agent system | Classifies, retrieves policy, cross-checks PA + history, decides, explains, escalates/flags |

## 4. In Scope (Phase 2)

- Claims for the same four services Phase 1 already has policy coverage for
  (MRI lumbar spine, CGM supplies, outpatient PT beyond 20 visits, bariatric
  surgery) — deliberately reusing the existing policy corpus rather than
  introducing new services to learn
- Cross-referencing a claim against an existing PA decision, where one exists
  (was this service pre-authorized, and does the claim match what was
  approved)
- Medical necessity / coding accuracy check against policy (same RAG pattern
  as Phase 1)
- Duplicate-billing detection (same service, same member, implausibly close
  together)
- A **fraud/anomaly pattern agent** reasoning over a provider's claim history
  — the one genuinely new agent type this phase requires, since Phase 1 has
  nothing like it (LLD Section 6's `get_prior_requests_for_member` was
  reserved for exactly this)
- Pay / deny-with-citation / flag-for-SIU decisioning
- Full audit logging, reusing Phase 1's audit architecture unchanged
- A claims examiner queue for flagged/escalated cases (the claims-side
  reviewer queue)

## 5. Out of Scope (Phase 2)

- Real EDI claim formats (837/835) or clearinghouse integration — this POC
  uses the same synthetic-data approach as Phase 1
- Multi-line/bundled claims (one claim, one service — same simplification
  Phase 1 made for PA)
- An appeals workflow for denied claims (would be a Phase 3 concern, mirroring
  the appeals/peer-to-peer agent flagged as a deferred extension for PA)
- Actual SIU case management (this system flags and hands off; it doesn't
  manage the investigation itself)

## 6. Core User Scenarios

**Scenario A — Clean pay, PA on file**
Claim matches an approved PA decision on file, coding is consistent with what
was authorized, no duplicate, no anomalous pattern. Agent pays automatically.

**Scenario B — Deny, doesn't meet medical necessity**
No PA on file (or PA was denied) for a service that required one, and the
claim itself doesn't independently meet the policy's medical necessity
criteria on review. Agent denies with a specific policy citation — same hard
requirement as Phase 1's deny path.

**Scenario C — Duplicate billing**
Same member, same service code, submitted twice within an implausible window.
Agent denies the duplicate, citing the original claim.

**Scenario D — Anomalous billing pattern, flagged for SIU**
The claim itself looks individually plausible, but the submitting provider's
recent claim history shows an anomalous pattern (e.g. an unusual volume of a
high-cost service, or a pattern of claims just below a review threshold).
Agent cannot resolve this from the single claim — it flags for SIU review with
a summary of the pattern it found, the same way Phase 1's Decision Agent
escalates rather than guesses.

**Scenario E — Escalation (borderline single-claim case)**
Same shape as Phase 1's escalation path: policy doesn't clearly resolve it, or
confidence is below threshold — routes to a claims examiner, not SIU (SIU is
specifically for suspected fraud/waste/abuse; a merely ambiguous case is a
different queue).

## 7. Success Metrics

| Metric | Target |
|---|---|
| Decision accuracy (pay/deny/flag) | Matches Phase 1's bar — measured the same way, via a held-out eval set with known outcomes |
| Denial citation correctness | Every deny traceable to a specific policy clause, same discipline as Phase 1 |
| False-flag rate (SIU) | Legitimate claims aren't flagged as suspected fraud — a false SIU flag has real provider-relations cost, not just a wasted review cycle |
| PA cross-reference accuracy | Correctly identifies when a matching PA exists vs. doesn't |
| Audit completeness | 100% of decisions logged with citation/reasoning, same as Phase 1 |

## 8. Non-Functional Requirements

- **Guardrails**: a new, narrowly-scoped DB role for the claims agent —
  read-only on claims/PA/member data, append-only on its own decisions and
  audit entries, exactly the pattern proven in Phase 1 (`pa_agent_role` /
  `pa_admin_role` / `pa_intake_role`), not a shortcut around it
- **Auditability**: identical bar to Phase 1 — every decision's reasoning and
  citation logged and queryable
- **Multi-turn**: much less central here than in PA — a claim is typically a
  single decision point, not a back-and-forth clarification loop. Where
  multi-turn matters is the SIU handoff (an investigator may come back with
  follow-up questions), not the initial pay/deny decision
- **Model flexibility**: reuse Phase 1's model gateway unchanged

## 9. Assumptions

- Reuses the same four policy documents and service codes from Phase 1 — no
  new policy corpus to build for this phase
- New synthetic data required: claims records (separate from PA requests, but
  linkable to them), a claims-specific table, and a provider claim-history
  dataset with deliberately-planted fraud/duplicate/anomaly patterns for eval
- Still a single hypothetical payer, still no real PHI, consistent with Phase
  1's Section 9 decision

## 10. What This Phase Reuses vs. Builds New

| Component | Reused from Phase 1 | New in Phase 2 |
|---|---|---|
| Policy RAG (4 policies) | ✅ unchanged | — |
| Decision engine pattern (structured output, citation validation, retry logic) | ✅ pattern reused | Outcome vocabulary changes: pay/deny/flag instead of approve/deny/escalate |
| DB-role guardrail pattern | ✅ pattern reused | New role(s) scoped to claims tables |
| Model gateway | ✅ unchanged | — |
| Audit logging | ✅ unchanged | — |
| Eval harness pattern | ✅ pattern reused | New test cases, new ground truth |
| Intake / completeness check | ✅ pattern reused | Claims-specific required-fields checklist |
| — | — | **Fraud/anomaly pattern agent** (reasons over claim history — genuinely new, nothing like it in Phase 1) |
| — | — | **PA-to-claim cross-reference logic** (genuinely new) |
| — | — | New DB tables: `claims`, `claim_documents` (or similar), possibly a `claim_decisions` table mirroring `pa_decisions` |
