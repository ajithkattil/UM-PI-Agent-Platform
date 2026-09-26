"""Runs all 8 eval/claims_test_cases.jsonl cases through the REAL claims
graph — actual Anthropic calls (coverage + fraud agents), actual Pinecone
retrieval, actual PA cross-reference against Phase 1's real pa_decisions.
This is the first point in Phase 2 where real reasoning gets measured, not
assumed — same milestone as Phase 1's run_eval.py.

IMPORTANT PREREQUISITE: the linked_pa_label scenarios only mean something
once Phase 1's pa_decisions table has real rows for the referenced PA
requests. If you haven't already, run Phase 1's eval/run_eval.py (or the UI)
first — otherwise those scenarios' PA cross-reference will show 'missing'
regardless of what the scenario's label implies, since there's genuinely no
PA decision on file yet.

Scores four things per Claims LLD Section 9:
  - decision accuracy: predicted outcome == expected outcome
  - citation correctness: for denies, does the citation contain an expected
    keyword
  - false-escalation rate: cases expected pay/deny that got escalated instead
  - false-SIU-flag rate: cases NOT expected flag_siu that got flagged
    anyway — tracked separately given the asymmetric real-world cost of a
    false SIU flag (HLD Section 7); this is the number SIU_FLAG_THRESHOLD
    should be tuned against, the same way ESCALATION_THRESHOLD was tuned
    from Phase 1's false-escalation rate
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.schemas import ClaimRequest, ClaimGraphState
from src.claims_orchestrator import build_claims_graph

EVAL_FILE = Path(__file__).resolve().parent.parent / "eval" / "claims_test_cases.jsonl"


def citation_matches(citation: str | None, expected_keywords: list[str]) -> bool:
    if not citation:
        return False
    citation_lower = citation.lower()
    return any(kw.lower() in citation_lower for kw in expected_keywords)


def run():
    cases = [json.loads(line) for line in EVAL_FILE.read_text().splitlines()]
    graph = build_claims_graph()  # real coverage_node, real pa_xref_node, real fraud_node

    correct_decisions = 0
    citation_checks_total = 0
    citation_checks_correct = 0
    false_escalations = 0
    false_siu_flags = 0
    results = []

    for case in cases:
        claim = ClaimRequest(
            claim_id=case["claim_id"], member_id=case["member_id"], provider_id=case["provider_id"],
            service_code=case["service_code"], service_description=case["service_description"],
            billed_amount=case["billed_amount"], date_of_service=case["date_of_service"],
            documents=case["documents"], clinical_notes=case["clinical_notes"],
        )

        state_out = graph.invoke(ClaimGraphState(claim=claim))
        decision = state_out.get("decision")
        status = state_out.get("status")

        if decision is None:
            # pending_docs or duplicate — short-circuited at intake, no
            # specialist agents ran, so status itself IS the outcome to score.
            actual_outcome = status
            actual_citation = None
        else:
            actual_outcome = decision.outcome if hasattr(decision, "outcome") else decision["outcome"]
            actual_citation = decision.citation if hasattr(decision, "citation") else decision["citation"]

        expected_outcome = case["expected_outcome"]
        decision_correct = actual_outcome == expected_outcome
        correct_decisions += decision_correct

        if expected_outcome == "deny":
            citation_checks_total += 1
            if citation_matches(actual_citation, case["expected_citation_keywords"]):
                citation_checks_correct += 1

        if expected_outcome in ("pay", "deny") and actual_outcome == "escalate":
            false_escalations += 1

        if expected_outcome != "flag_siu" and actual_outcome == "flag_siu":
            false_siu_flags += 1

        results.append({
            "label": case["label"], "expected": expected_outcome, "actual": actual_outcome,
            "correct": decision_correct, "citation": actual_citation,
        })

        marker = "PASS" if decision_correct else "FAIL"
        print(f"[{marker}] {case['label']:38s} expected={expected_outcome:10s} actual={actual_outcome:10s}")
        if actual_citation:
            print(f"         citation: {actual_citation}")

    n = len(cases)
    print(f"\nDecision accuracy: {correct_decisions}/{n} ({100*correct_decisions/n:.0f}%)")
    if citation_checks_total:
        print(f"Citation correctness (deny cases): {citation_checks_correct}/{citation_checks_total} "
              f"({100*citation_checks_correct/citation_checks_total:.0f}%)")

    clean_cases = sum(1 for c in cases if c["expected_outcome"] in ("pay", "deny"))
    if clean_cases:
        print(f"False-escalation rate: {false_escalations}/{clean_cases} "
              f"({100*false_escalations/clean_cases:.0f}%)")

    non_siu_cases = sum(1 for c in cases if c["expected_outcome"] != "flag_siu")
    if non_siu_cases:
        print(f"False-SIU-flag rate: {false_siu_flags}/{non_siu_cases} "
              f"({100*false_siu_flags/non_siu_cases:.0f}%) — tune SIU_FLAG_THRESHOLD against this")

    out_path = Path(__file__).resolve().parent / "claims_eval_results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nFull results written to {out_path}")


if __name__ == "__main__":
    run()
