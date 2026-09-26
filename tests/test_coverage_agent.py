"""Tests coverage_node's citation-validation/retry logic for real — real DB
writes (audit_log via claims_agent_role), scripted fake model and stubbed
policy retrieval standing in for the two things this sandbox has no live
credentials for (same approach as Phase 1's test_orchestrator_routing.py).
"""

import json
import sys
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.schemas import ClaimRequest, ClaimGraphState, PolicyChunk
from src.agents.coverage_agent import coverage_node
from src.tools.claims_db_tools import get_claim


def stub_retrieve_policy(service_code, request_date):
    return [PolicyChunk(text="stub policy text", source_file="stub.md", section_header="Coverage Criteria")]


def make_scripted_llm(script):
    calls = iter(script)
    return lambda prompt: json.dumps(next(calls))


def get_real_claim():
    """Uses one of the actual seeded claims so the audit write has a real
    claim_id to satisfy the FK, same discipline as Phase 1's tests."""
    claim_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "claim-claim_pay_matches_approved_mri"))
    row = get_claim(claim_id)
    return ClaimRequest(
        claim_id=row["claim_id"], member_id=row["member_id"], provider_id=row["provider_id"],
        service_code=row["service_code"], service_description=row["service_description"],
        billed_amount=float(row["billed_amount"]), date_of_service=row["date_of_service"],
        documents=[], clinical_notes=row["clinical_notes"],
    )


def run_case(label, llm_script, expect_flagged_outcome, expect_min_confidence=None):
    claim = get_real_claim()
    state = ClaimGraphState(claim=claim)
    result = coverage_node(
        state, llm_call_fn=make_scripted_llm(llm_script), retrieve_policy_fn=stub_retrieve_policy,
    )
    signal = result.coverage_signal
    ok = signal.flagged_outcome == expect_flagged_outcome
    if expect_min_confidence is not None:
        ok = ok and signal.confidence >= expect_min_confidence
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: flagged_outcome={signal.flagged_outcome} "
          f"confidence={signal.confidence} citation={signal.citation}")
    return ok


def run():
    results = []

    results.append(run_case(
        "clean_approve", [{"outcome": "approve", "citation": None, "confidence": 0.95,
                            "reasoning_summary": "meets all criteria"}],
        expect_flagged_outcome="approve",
    ))

    results.append(run_case(
        "clean_deny_with_citation",
        [{"outcome": "deny", "citation": "Coverage Criteria, item 2", "confidence": 0.9,
          "reasoning_summary": "does not meet criteria"}],
        expect_flagged_outcome="deny",
    ))

    results.append(run_case(
        "deny_missing_citation_then_corrected",
        [
            {"outcome": "deny", "citation": None, "confidence": 0.9, "reasoning_summary": "no"},
            {"outcome": "deny", "citation": "Exclusions section", "confidence": 0.9, "reasoning_summary": "no, cited"},
        ],
        expect_flagged_outcome="deny",
    ))

    results.append(run_case(
        "deny_missing_citation_twice_forces_low_confidence",
        [
            {"outcome": "deny", "citation": None, "confidence": 0.9, "reasoning_summary": "no"},
            {"outcome": "deny", "citation": None, "confidence": 0.9, "reasoning_summary": "still no"},
        ],
        expect_flagged_outcome="unresolved", expect_min_confidence=None,
    ))

    print(f"\n{sum(results)}/{len(results)} coverage agent scenarios correct")
    if not all(results):
        sys.exit(1)


if __name__ == "__main__":
    run()
