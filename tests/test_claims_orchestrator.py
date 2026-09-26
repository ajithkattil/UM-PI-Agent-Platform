"""Tests the actual compiled claims graph end-to-end. The critical thing
this verifies empirically, not just assumes: that reconcile_node runs
EXACTLY ONCE with all three specialist signals populated — not once per
predecessor with partial state. If the join were broken, reconcile() would
either crash immediately (calling .flagged_outcome on a None signal) or
write multiple claim_decisions rows for one claim; both are checked below.
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.schemas import ClaimRequest, ClaimGraphState, SpecialistSignal
from src.claims_orchestrator import build_claims_graph
from src.tools.claims_db_tools import _connect as claims_connect


def stub_coverage_approve(state):
    state.coverage_signal = SpecialistSignal(agent_name="coverage", flagged_outcome="approve",
                                              confidence=0.9, reasoning_summary="stub")
    return state


def stub_pa_xref_matches(state):
    state.pa_xref_signal = SpecialistSignal(agent_name="pa_xref", flagged_outcome="matches",
                                             confidence=0.95, reasoning_summary="stub")
    return state


def stub_fraud_clear(state):
    state.fraud_signal = SpecialistSignal(agent_name="fraud", flagged_outcome="clear",
                                           confidence=0.9, reasoning_summary="stub")
    return state


def count_decisions_for_claim(claim_id: str) -> int:
    with claims_connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM claim_decisions WHERE claim_id = %s", (claim_id,))
        return cur.fetchone()[0]


def run():
    results = []
    graph = build_claims_graph(
        coverage_node_fn=stub_coverage_approve,
        pa_xref_node_fn=stub_pa_xref_matches,
        fraud_node_fn=stub_fraud_clear,
    )

    # 1. Full pipeline: a real, complete claim -> all three stubs run ->
    # reconcile joins correctly -> pay
    real_claim_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "claim-claim_pay_matches_approved_mri"))
    decisions_before = count_decisions_for_claim(real_claim_id)

    claim = ClaimRequest(
        claim_id=real_claim_id, member_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "member-1")),
        provider_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "prov-1")), service_code="72148",
        service_description="MRI Lumbar Spine without contrast", billed_amount=850.00,
        date_of_service="2026-09-21", documents=[], clinical_notes="test",
    )
    result = graph.invoke(ClaimGraphState(claim=claim))

    has_all_three = (result.get("coverage_signal") is not None
                      and result.get("pa_xref_signal") is not None
                      and result.get("fraud_signal") is not None)
    ok = has_all_three
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] join_produces_all_three_signals: {has_all_three}")

    decision = result.get("decision")
    outcome = decision.outcome if hasattr(decision, "outcome") else decision["outcome"]
    ok = outcome == "pay"
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] clean_signals_produce_pay: {outcome}")

    decisions_after = count_decisions_for_claim(real_claim_id)
    ok = decisions_after == decisions_before + 1  # exactly one new row, not three
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] reconcile_wrote_exactly_one_decision: "
          f"before={decisions_before} after={decisions_after}")

    # 2. Pending docs — real intake logic, no specialists should ever run
    pending_claim = ClaimRequest(
        claim_id=str(uuid.uuid4()),  # nonexistent claim_id -> get_claim_documents returns []
        member_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "member-1")),
        provider_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "prov-1")), service_code="72148",
        service_description="MRI Lumbar Spine without contrast", billed_amount=850.00,
        date_of_service="2026-09-21", documents=[], clinical_notes="test",
    )
    result2 = graph.invoke(ClaimGraphState(claim=pending_claim))
    ok = result2["status"] == "pending_docs" and result2.get("decision") is None
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] pending_docs_short_circuits: status={result2['status']}")

    # 3. Duplicate — real intake logic against the real seeded duplicate pair
    dup_claim_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "claim-claim_duplicate_original"))
    dup_claim = ClaimRequest(
        claim_id=dup_claim_id, member_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "member-5")),
        provider_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "prov-2")), service_code="A4239",
        service_description="Continuous Glucose Monitor supplies", billed_amount=120.00,
        date_of_service="2026-09-20", documents=[], clinical_notes="test",
    )
    result3 = graph.invoke(ClaimGraphState(claim=dup_claim))
    ok = result3["status"] == "duplicate" and result3.get("decision") is None
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] duplicate_short_circuits: status={result3['status']}")

    print(f"\n{sum(results)}/{len(results)} orchestrator scenarios correct")
    if not all(results):
        sys.exit(1)


if __name__ == "__main__":
    run()
