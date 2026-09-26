"""Reconciliation (Claims LLD Section 6) — combines the three specialist
signals into one ClaimDecision.

Deliberately NOT an LLM call — this is the confirmed design decision:
compounding uncertainty on top of three already-uncertain signals would make
the final decision both harder to audit and harder to explain to a
compliance officer than a fixed, auditable priority order. Every branch here
is independently unit-testable with scripted fake signals (no live model or
DB credentials needed), same trick as Phase 1's test_orchestrator_routing.py.

Priority order (confirmed):
  1. Fraud flagged above SIU_FLAG_THRESHOLD -> flag_siu, overrides everything
     else — a claim can be individually clean and still be part of a fraud
     pattern; fraud holds override routine adjudication.
  2. PA missing or explicitly denied -> deny, citing the PA problem
  3. Coverage agent denies independently -> deny, citing the policy clause
  4. Any signal's confidence below CLAIMS_ESCALATION_THRESHOLD -> escalate
  5. Otherwise -> pay
"""

from src.schemas import SpecialistSignal, ClaimDecision
from src import config

# pa_xref flagged_outcome values that mean "this claim should be denied on
# PA grounds" — "mismatch" and "pending" are deliberately NOT here; both
# were given confidence below CLAIMS_ESCALATION_THRESHOLD by pa_xref_agent,
# so they fall through to the confidence-based escalate rule instead of
# being hard-denied — a mismatched or pending PA is genuinely ambiguous,
# not a clear-cut deny.
PA_DENY_OUTCOMES = {"missing", "denied"}


def reconcile(coverage: SpecialistSignal, pa_xref: SpecialistSignal,
              fraud: SpecialistSignal) -> ClaimDecision:
    # 1. Fraud override
    if fraud.flagged_outcome == "flagged" and fraud.confidence >= config.SIU_FLAG_THRESHOLD:
        return ClaimDecision(
            outcome="flag_siu", citation=fraud.reasoning_summary, confidence=fraud.confidence,
            coverage_signal=coverage, pa_xref_signal=pa_xref, fraud_signal=fraud,
        )

    # 2. PA problem -> deny
    if pa_xref.flagged_outcome in PA_DENY_OUTCOMES:
        decision = ClaimDecision(
            outcome="deny", citation=pa_xref.citation, confidence=pa_xref.confidence,
            coverage_signal=coverage, pa_xref_signal=pa_xref, fraud_signal=fraud,
        )
        return decision if decision.validate_citation_rule() else _forced_escalate(
            coverage, pa_xref, fraud, "pa_xref denied without a citation")

    # 3. Coverage denies independently (even if PA matches)
    if coverage.flagged_outcome == "deny":
        decision = ClaimDecision(
            outcome="deny", citation=coverage.citation, confidence=coverage.confidence,
            coverage_signal=coverage, pa_xref_signal=pa_xref, fraud_signal=fraud,
        )
        return decision if decision.validate_citation_rule() else _forced_escalate(
            coverage, pa_xref, fraud, "coverage denied without a citation")

    # 4. Confidence-gated escalation
    lowest_confidence = min(coverage.confidence, pa_xref.confidence, fraud.confidence)
    if lowest_confidence < config.CLAIMS_ESCALATION_THRESHOLD:
        return ClaimDecision(
            outcome="escalate", citation=None, confidence=lowest_confidence,
            coverage_signal=coverage, pa_xref_signal=pa_xref, fraud_signal=fraud,
        )

    # 5. Clean pay
    return ClaimDecision(
        outcome="pay", citation=None, confidence=lowest_confidence,
        coverage_signal=coverage, pa_xref_signal=pa_xref, fraud_signal=fraud,
    )


def _forced_escalate(coverage, pa_xref, fraud, reason: str) -> ClaimDecision:
    # Defensive net, same philosophy as Phase 1's hard citation constraint —
    # this should never actually trigger given the upstream agents already
    # guarantee a citation whenever flagged_outcome is a deny-eligible value,
    # but reconciliation never lets an invalid deny through even if that
    # upstream guarantee is ever violated by a future change.
    return ClaimDecision(
        outcome="escalate", citation=None, confidence=0.0,
        coverage_signal=coverage, pa_xref_signal=pa_xref, fraud_signal=fraud,
    )
