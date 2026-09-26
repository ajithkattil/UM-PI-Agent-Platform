"""Tests pa_xref_node's five branches (missing / denied / pending / matches /
mismatch) against the live database. Since this is pure DB lookup logic, no
scripted fake model is needed — but it DOES need known, clean pa_decisions
rows to check against, so this seeds deterministic test fixtures first
(clearly marked decided_by='xref_test_seed', never mistaken for real agent
or reviewer output) rather than relying on whatever accumulated from
earlier ad-hoc testing.
"""

import json
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.schemas import ClaimRequest, ClaimGraphState
from src.agents.pa_xref_agent import pa_xref_node
from src.tools.db_tools import record_decision

EVAL_FILE = Path(__file__).resolve().parent.parent / "eval" / "claims_test_cases.jsonl"


def pa_req_id(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"req-{label}"))


# label -> (outcome, citation, decided_days_before_2026-09-24)
# Dates chosen so every linked claim's date_of_service falls AFTER its PA's
# decided_at, i.e. a clean "matches" — except we don't seed one for mismatch,
# that's tested directly below without touching the DB at all.
PA_SEED = {
    "mri_approve_standard": ("approve", "Coverage Criteria met", 10),
    "mri_deny_insufficient_duration": ("deny", "Coverage Criteria, item 1 not met", 10),
    "cgm_approve": ("approve", "Coverage Criteria met", 10),
    "pt_approve_beyond_20": ("approve", "Coverage Criteria met", 15),
    "cgm_escalate_newly_diagnosed": ("escalate", None, 10),
}


def seed_pa_decisions():
    from datetime import datetime, timezone

    # This test needs FULLY controlled fixtures to be deterministic — your
    # real pa_decisions rows (from repeated eval/run_eval.py runs) are all
    # more recent than these seeded timestamps, so without clearing them
    # first, get_pa_decision_for_service correctly picks YOUR real, newer
    # decision instead of this test's fixture — which is the right behavior
    # for production code, just not what this isolated unit test wants.
    #
    # This can't be done from inside the script: pa_agent_role deliberately
    # has no DELETE/TRUNCATE privilege on pa_decisions (that's the guardrail
    # working correctly, not a bug), so clearing the table requires your own
    # full-privilege psql session, same as every other DB reset in this
    # project. Run this yourself BEFORE running this test:
    #
    #   psql -d pa_agent_poc -c "TRUNCATE pa_decisions;"
    #
    # CONSEQUENCE: that wipes your real Phase 1 eval history. Re-run Phase
    # 1's eval/run_eval.py afterward if you want that data back before
    # running Phase 2's real eval/run_claims_eval.py again.

    seed_now = datetime(2026, 9, 24, tzinfo=timezone.utc)  # matches CLAIMS_NOW in generate_synthetic_claims.py
    for label, (outcome, citation, days_before) in PA_SEED.items():
        record_decision(
            request_id=pa_req_id(label), outcome=outcome, citation=citation,
            confidence=0.9, decided_by="xref_test_seed",
            decided_at=seed_now - timedelta(days=days_before),
        )
    print(f"Seeded {len(PA_SEED)} known PA decisions for testing.\n")


EXPECTED_FLAGGED_OUTCOME = {
    "claim_pay_matches_approved_mri": "matches",
    "claim_pay_matches_approved_cgm": "matches",
    "claim_deny_pa_was_denied": "denied",
    "claim_deny_no_pa_on_file": "missing",
    "claim_deny_coverage_independent_of_pa": "matches",
    "claim_duplicate_original": "matches",
    "claim_escalate_ambiguous": "pending",
    "claim_flag_siu_volume_anomaly": "missing",
}


def run():
    seed_pa_decisions()
    cases = [json.loads(line) for line in EVAL_FILE.read_text().splitlines()]

    passed, failed = 0, 0
    for case in cases:
        claim = ClaimRequest(
            claim_id=case["claim_id"], member_id=case["member_id"], provider_id=case["provider_id"],
            service_code=case["service_code"], service_description=case["service_description"],
            billed_amount=case["billed_amount"], date_of_service=case["date_of_service"],
            documents=case["documents"], clinical_notes=case["clinical_notes"],
        )
        state = ClaimGraphState(claim=claim)
        result = pa_xref_node(state)

        expected = EXPECTED_FLAGGED_OUTCOME[case["label"]]
        ok = result.pa_xref_signal.flagged_outcome == expected
        passed += ok
        failed += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] {case['label']:38s} "
              f"expected={expected:10s} actual={result.pa_xref_signal.flagged_outcome}")

    # Mismatch branch — tested directly, no DB seeding needed: construct a
    # claim whose date_of_service is deliberately BEFORE its linked PA's
    # decided_at (mri_approve_standard, seeded 10 days before 2026-09-24 above).
    mismatch_claim = ClaimRequest(
        claim_id=str(uuid.uuid4()), member_id="test", provider_id="test",
        service_code="72148", service_description="MRI Lumbar Spine without contrast",
        billed_amount=850.00, date_of_service=date(2000, 1, 1),  # absurdly early -> before any PA decision
        documents=[], clinical_notes="mismatch branch test",
    )
    # This claim's member_id "test" won't match mri_approve_standard's real
    # member — so instead we directly verify the branch logic by checking
    # a claim linked to the SAME member as claim_pay_matches_approved_mri
    # but with an impossible date_of_service.
    real_case = next(c for c in cases if c["label"] == "claim_pay_matches_approved_mri")
    mismatch_claim = ClaimRequest(
        claim_id=str(uuid.uuid4()), member_id=real_case["member_id"], provider_id=real_case["provider_id"],
        service_code="72148", service_description="MRI Lumbar Spine without contrast",
        billed_amount=850.00, date_of_service=date(2000, 1, 1),
        documents=[], clinical_notes="mismatch branch test",
    )
    result = pa_xref_node(ClaimGraphState(claim=mismatch_claim))
    ok = result.pa_xref_signal.flagged_outcome == "mismatch"
    passed += ok
    failed += not ok
    print(f"[{'PASS' if ok else 'FAIL'}] mismatch_branch_direct_test          "
          f"expected=mismatch   actual={result.pa_xref_signal.flagged_outcome}")

    print(f"\n{passed}/{passed + failed} scenarios correct")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    run()
