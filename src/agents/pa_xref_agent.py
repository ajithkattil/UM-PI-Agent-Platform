"""PA Cross-Reference Agent (Claims HLD Section 4.4 / LLD Section 5,
pa_xref_node).

Deliberately NOT an LLM call — "does a matching approved PA exist for this
claim" is a fact lookup against Phase 1's own pa_decisions table, not a
judgment call. This is the literal code-level expression of the combined
architecture diagram's dotted line: Phase 2 reading Phase 1's real output.

Five possible signals, in order of how directly they're a fact lookup vs. a
plausibility judgment:
  - missing   : no PA request/decision exists at all for this member+service
  - denied    : a PA exists and was denied
  - pending   : a PA exists but was itself escalated and never resolved —
                genuinely ambiguous, not a clean "found" or "missing"
  - mismatch  : an approved PA exists, but the claim's date_of_service
                precedes the PA decision date (service happened before
                authorization — implausible, worth a lower confidence)
  - matches   : an approved PA exists and the dates are plausible
"""

from src.schemas import ClaimGraphState, SpecialistSignal
from src.audit import write_audit_entry
from src.tools.claims_db_tools import get_pa_decision_for_service, _connect as claims_connect


def pa_xref_node(state: ClaimGraphState) -> ClaimGraphState:
    claim = state.claim
    pa_decision = get_pa_decision_for_service(claim.member_id, claim.service_code)

    if pa_decision is None:
        signal = SpecialistSignal(
            agent_name="pa_xref", flagged_outcome="missing",
            citation="No prior authorization on file for a service that requires one.",
            confidence=0.95,
            reasoning_summary="No matching PA request/decision found for this member and service.",
        )
    elif pa_decision["outcome"] == "deny":
        signal = SpecialistSignal(
            agent_name="pa_xref", flagged_outcome="denied",
            citation=f"Prior authorization was denied: {pa_decision['citation'] or 'no reason on file'}",
            confidence=0.95,
            reasoning_summary="A prior authorization request for this service exists but was denied.",
        )
    elif pa_decision["outcome"] == "escalate":
        signal = SpecialistSignal(
            agent_name="pa_xref", flagged_outcome="pending", citation=None, confidence=0.4,
            reasoning_summary="A prior authorization request for this service was escalated and never resolved to approve or deny.",
        )
    elif pa_decision["outcome"] == "approve":
        decided_date = pa_decision["decided_at"].date()
        if claim.date_of_service < decided_date:
            signal = SpecialistSignal(
                agent_name="pa_xref", flagged_outcome="mismatch",
                citation="Service date precedes the prior authorization decision date.",
                confidence=0.5,
                reasoning_summary=f"Claim date of service ({claim.date_of_service}) is before "
                                   f"the PA was decided ({decided_date}) — service could not "
                                   f"have been authorized in advance as claimed.",
            )
        else:
            signal = SpecialistSignal(
                agent_name="pa_xref", flagged_outcome="matches", citation=None, confidence=0.95,
                reasoning_summary="Approved prior authorization on file, dates are consistent with this claim.",
            )
    else:
        signal = SpecialistSignal(
            agent_name="pa_xref", flagged_outcome="unresolved", citation=None, confidence=0.0,
            reasoning_summary=f"Unrecognized PA decision outcome on file: {pa_decision['outcome']!r}",
        )

    write_audit_entry(
        request_id=claim.claim_id,
        step="pa_xref_agent",
        detail={
            "flagged_outcome": signal.flagged_outcome,
            "citation": signal.citation,
            "confidence": signal.confidence,
            "pa_decision_found": pa_decision is not None,
            "pa_decision_outcome": pa_decision["outcome"] if pa_decision else None,
        },
        connect_fn=claims_connect,
    )

    state.pa_xref_signal = signal
    return state
