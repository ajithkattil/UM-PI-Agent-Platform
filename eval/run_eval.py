"""Runs all 14 eval/test_cases.jsonl cases through the REAL decision agent —
actual Anthropic model, actual Pinecone retrieval. This is the first point
anywhere in this project where actual LLM reasoning quality gets measured
rather than assumed; tests/test_orchestrator_routing.py proved the plumbing
around it works, using a scripted fake model. This proves the model itself.

Scores three things per LLD Section 7:
  - decision accuracy: predicted outcome == expected outcome
  - citation correctness: for denies, does the citation contain an expected keyword
  - false-escalation rate: cases expected to be a clean approve/deny that got
    escalated instead — this is the number ESCALATION_THRESHOLD should be
    tuned against
"""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.schemas import PARequest, GraphState
from src.orchestrator import build_graph

EVAL_FILE = Path(__file__).resolve().parent.parent / "eval" / "test_cases.jsonl"


def citation_matches(citation: str | None, expected_keywords: list[str]) -> bool:
    if not citation:
        return False
    citation_lower = citation.lower()
    return any(kw.lower() in citation_lower for kw in expected_keywords)


def run():
    cases = [json.loads(line) for line in EVAL_FILE.read_text().splitlines()]
    graph = build_graph()  # real decision_node, real policy_retrieval_node

    correct_decisions = 0
    citation_checks_total = 0
    citation_checks_correct = 0
    false_escalations = 0
    results = []

    for case in cases:
        request = PARequest(
            request_id=case["request_id"],
            member_id=case["member_id"],
            provider_id=case["provider_id"],
            service_code=case["service_code"],
            service_description=case["service_description"],
            request_type=case["request_type"],
            documents=case["documents"],
            clinical_notes=case["clinical_summary"],
        )

        state_out = graph.invoke(GraphState(request=request))
        decision = state_out.get("decision")
        chunks_retrieved = len(state_out.get("retrieved_policy", []))

        if decision is None:
            actual_outcome = "pending_docs"
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

        if expected_outcome in ("approve", "deny") and actual_outcome == "escalate":
            false_escalations += 1

        results.append({
            "label": case["label"],
            "expected": expected_outcome,
            "actual": actual_outcome,
            "correct": decision_correct,
            "citation": actual_citation,
        })

        marker = "PASS" if decision_correct else "FAIL"
        print(f"[{marker}] {case['label']:38s} expected={expected_outcome:12s} actual={actual_outcome:12s} chunks={chunks_retrieved}")
        if actual_citation:
            print(f"         citation: {actual_citation}")

    n = len(cases)
    print(f"\nDecision accuracy: {correct_decisions}/{n} ({100*correct_decisions/n:.0f}%)")
    if citation_checks_total:
        print(f"Citation correctness (deny cases): {citation_checks_correct}/{citation_checks_total} "
              f"({100*citation_checks_correct/citation_checks_total:.0f}%)")
    clean_cases = sum(1 for c in cases if c["expected_outcome"] in ("approve", "deny"))
    print(f"False-escalation rate: {false_escalations}/{clean_cases} "
          f"({100*false_escalations/clean_cases:.0f}%) — tune ESCALATION_THRESHOLD against this")

    out_path = Path(__file__).resolve().parent / "eval_results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nFull results written to {out_path}")


if __name__ == "__main__":
    run()
