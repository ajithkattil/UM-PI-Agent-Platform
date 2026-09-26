"""Tests reconcile()'s priority order directly with scripted SpecialistSignal
objects — no database, no LLM, no live credentials needed at all. This is
the cleanest layer in the whole project to test exhaustively, since it's a
pure function.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.schemas import SpecialistSignal
from src.agents.reconciliation import reconcile


def sig(agent_name, flagged_outcome, confidence, citation=None, reasoning="test"):
    return SpecialistSignal(agent_name=agent_name, flagged_outcome=flagged_outcome,
                             citation=citation, confidence=confidence, reasoning_summary=reasoning)


def run():
    results = []

    # 1. Clean pay
    d = reconcile(sig("coverage", "approve", 0.9), sig("pa_xref", "matches", 0.95),
                  sig("fraud", "clear", 0.9))
    ok = d.outcome == "pay"
    results.append(ok); print(f"[{'PASS' if ok else 'FAIL'}] clean_pay: {d.outcome}")

    # 2. Fraud override — flags SIU even though coverage/pa_xref both look clean
    d = reconcile(sig("coverage", "approve", 0.9), sig("pa_xref", "matches", 0.95),
                  sig("fraud", "flagged", 0.9, reasoning="10x peer median volume"))
    ok = d.outcome == "flag_siu"
    results.append(ok); print(f"[{'PASS' if ok else 'FAIL'}] fraud_override: {d.outcome}")

    # 3. PA missing -> deny citing pa_xref
    d = reconcile(sig("coverage", "approve", 0.9),
                  sig("pa_xref", "missing", 0.95, citation="No PA on file"),
                  sig("fraud", "clear", 0.9))
    ok = d.outcome == "deny" and d.citation == "No PA on file"
    results.append(ok); print(f"[{'PASS' if ok else 'FAIL'}] pa_missing_denies: {d.outcome} / {d.citation}")

    # 4. PA denied -> deny citing pa_xref
    d = reconcile(sig("coverage", "approve", 0.9),
                  sig("pa_xref", "denied", 0.95, citation="PA was denied: insufficient duration"),
                  sig("fraud", "clear", 0.9))
    ok = d.outcome == "deny" and "denied" in d.citation
    results.append(ok); print(f"[{'PASS' if ok else 'FAIL'}] pa_denied_denies: {d.outcome} / {d.citation}")

    # 5. Coverage denies independently, even with a matching approved PA
    d = reconcile(sig("coverage", "deny", 0.9, citation="Exclusions: missing progress notes"),
                  sig("pa_xref", "matches", 0.95), sig("fraud", "clear", 0.9))
    ok = d.outcome == "deny" and "progress notes" in d.citation
    results.append(ok); print(f"[{'PASS' if ok else 'FAIL'}] coverage_denies_despite_matching_pa: {d.outcome} / {d.citation}")

    # 6. Low confidence on any one signal -> escalate
    d = reconcile(sig("coverage", "approve", 0.5), sig("pa_xref", "matches", 0.95),
                  sig("fraud", "clear", 0.95))
    ok = d.outcome == "escalate"
    results.append(ok); print(f"[{'PASS' if ok else 'FAIL'}] low_confidence_escalates: {d.outcome}")

    # 7. Fraud flagged but BELOW the SIU threshold — should not override,
    # but its low confidence still triggers ordinary escalation instead
    d = reconcile(sig("coverage", "approve", 0.9), sig("pa_xref", "matches", 0.95),
                  sig("fraud", "flagged", 0.6))
    ok = d.outcome == "escalate"  # not flag_siu — 0.6 < SIU_FLAG_THRESHOLD (0.85)
    results.append(ok); print(f"[{'PASS' if ok else 'FAIL'}] fraud_flagged_below_siu_threshold_escalates_not_flags: {d.outcome}")

    # 8. Defensive net: a deny-eligible signal with NO citation (violating
    # the upstream guarantee deliberately) must never leak through as deny
    d = reconcile(sig("coverage", "approve", 0.9),
                  sig("pa_xref", "denied", 0.95, citation=None),  # malformed on purpose
                  sig("fraud", "clear", 0.9))
    ok = d.outcome == "escalate" and d.citation is None
    results.append(ok); print(f"[{'PASS' if ok else 'FAIL'}] defensive_net_catches_uncited_deny: {d.outcome}")

    print(f"\n{sum(results)}/{len(results)} reconciliation scenarios correct")
    if not all(results):
        sys.exit(1)


if __name__ == "__main__":
    run()
