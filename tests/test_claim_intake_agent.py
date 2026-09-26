"""Runs the actual claim_intake_node against the live, guardrail-restricted
database for all 8 synthetic claim scenarios and checks the
dispatched/pending_docs/duplicate classification matches
eval/claims_test_cases.jsonl's expectations.

None of the 8 scenarios test a missing-documentation case (that path is
covered structurally by Phase 1's identical pattern in
tests/test_intake_agent.py) — this test's real job is proving the DUPLICATE
detection works correctly against real submitted_at ordering, which is the
one thing genuinely new here.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.schemas import ClaimRequest, ClaimGraphState
from src.agents.claim_intake_agent import claim_intake_node

EVAL_FILE = Path(__file__).resolve().parent.parent / "eval" / "claims_test_cases.jsonl"


def run():
    cases = [json.loads(line) for line in EVAL_FILE.read_text().splitlines()]

    passed, failed = 0, 0
    for case in cases:
        claim = ClaimRequest(
            claim_id=case["claim_id"],
            member_id=case["member_id"],
            provider_id=case["provider_id"],
            service_code=case["service_code"],
            service_description=case["service_description"],
            billed_amount=case["billed_amount"],
            date_of_service=case["date_of_service"],
            documents=case["documents"],
            clinical_notes=case["clinical_notes"],
        )
        state = ClaimGraphState(claim=claim)
        result = claim_intake_node(state)

        expected_status = "duplicate" if case["expected_outcome"] == "duplicate" else "dispatched"
        ok = result.status == expected_status
        passed += ok
        failed += not ok

        marker = "PASS" if ok else "FAIL"
        print(
            f"[{marker}] {case['label']:38s} "
            f"expected={expected_status:12s} actual={result.status:12s} "
            f"duplicate_of={result.duplicate_of_claim_id}"
        )

    print(f"\n{passed}/{passed + failed} scenarios correct")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    run()
