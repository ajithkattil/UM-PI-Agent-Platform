"""Runs the actual intake_node against the live, guardrail-restricted database
for all 14 synthetic scenarios and checks the pending_docs/proceeds split
matches what eval/test_cases.jsonl expects.

This is the one part of the pipeline testable end-to-end without any LLM
provider configured — it exercises real DB I/O through the restricted
pa_agent_role connection, not a mock.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.schemas import PARequest, GraphState
from src.agents.intake_agent import intake_node

EVAL_FILE = Path(__file__).resolve().parent.parent / "eval" / "test_cases.jsonl"


def run():
    cases = [json.loads(line) for line in EVAL_FILE.read_text().splitlines()]

    passed, failed = 0, 0
    for case in cases:
        request = PARequest(
            request_id=case["request_id"],
            member_id=case["member_id"],
            provider_id=case["provider_id"],
            service_code=case["service_code"],
            service_description=case["service_description"],
            request_type=case["request_type"],
            documents=case["documents"],
        )
        state = GraphState(request=request)
        result = intake_node(state)

        expected_pending = case["expected_outcome"] == "pending_docs"
        actual_pending = result.status == "pending_docs"

        ok = expected_pending == actual_pending
        passed += ok
        failed += not ok

        marker = "PASS" if ok else "FAIL"
        print(
            f"[{marker}] {case['label']:38s} "
            f"expected_pending={expected_pending!s:5} actual_pending={actual_pending!s:5} "
            f"missing={result.missing_documents}"
        )

    print(f"\n{passed}/{passed + failed} scenarios correct")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    run()
