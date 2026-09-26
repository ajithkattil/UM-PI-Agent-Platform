"""Tests fraud_node's citation-validation/retry logic. Uses REAL
compute_signals() (already proven against the live database — the planted
10x volume anomaly for Dr. Elena Ruiz vs. a normal provider) combined with a
scripted fake model standing in for the one thing this sandbox has no live
credentials for.
"""

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.schemas import ClaimRequest, ClaimGraphState
from src.agents.fraud_agent import fraud_node


def uid(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))


def make_scripted_llm(script):
    calls = iter(script)
    return lambda prompt: json.dumps(next(calls))


def make_claim(provider_seed: str, service_code: str) -> ClaimRequest:
    return ClaimRequest(
        claim_id=str(uuid.uuid4()), member_id=uid("member-1"),
        provider_id=uid(provider_seed), service_code=service_code,
        service_description="test", billed_amount=850.00,
        date_of_service="2026-09-20", documents=[], clinical_notes="test",
    )


def run_case(label, provider_seed, service_code, llm_script, expect_flagged_outcome):
    claim = make_claim(provider_seed, service_code)
    state = ClaimGraphState(claim=claim)
    result = fraud_node(state, llm_call_fn=make_scripted_llm(llm_script))
    signal = result.fraud_signal
    ok = signal.flagged_outcome == expect_flagged_outcome
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: flagged_outcome={signal.flagged_outcome} "
          f"confidence={signal.confidence} citation={signal.citation} "
          f"metrics={signal.computed_metrics}")
    return ok


def run():
    results = []

    # Real computed signals for the normal-volume provider — model correctly
    # says clear given genuinely unremarkable numbers.
    results.append(run_case(
        "normal_provider_clear", "prov-2", "72148",
        [{"outcome": "clear", "citation": None, "confidence": 0.9,
          "reasoning_summary": "volume within normal range"}],
        expect_flagged_outcome="clear",
    ))

    # Real computed signals for the anomalous provider — model correctly
    # flags, citing the real 10x ratio.
    results.append(run_case(
        "anomalous_provider_flagged_with_citation", "prov-1", "72148",
        [{"outcome": "flagged", "citation": "10.0x peer median claim volume", "confidence": 0.9,
          "reasoning_summary": "provider billing far above peer volume"}],
        expect_flagged_outcome="flagged",
    ))

    # Flagged without citation -> retry -> corrected
    results.append(run_case(
        "flagged_missing_citation_then_corrected", "prov-1", "72148",
        [
            {"outcome": "flagged", "citation": None, "confidence": 0.9, "reasoning_summary": "suspicious"},
            {"outcome": "flagged", "citation": "10.0x peer median", "confidence": 0.9, "reasoning_summary": "corrected"},
        ],
        expect_flagged_outcome="flagged",
    ))

    # Flagged without citation TWICE -> forced low confidence, not a leaked flag
    results.append(run_case(
        "flagged_missing_citation_twice_forces_low_confidence", "prov-1", "72148",
        [
            {"outcome": "flagged", "citation": None, "confidence": 0.9, "reasoning_summary": "suspicious"},
            {"outcome": "flagged", "citation": None, "confidence": 0.9, "reasoning_summary": "still suspicious"},
        ],
        expect_flagged_outcome="unresolved",
    ))

    print(f"\n{sum(results)}/{len(results)} fraud agent scenarios correct")
    if not all(results):
        sys.exit(1)


if __name__ == "__main__":
    run()
