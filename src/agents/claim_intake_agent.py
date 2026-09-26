"""Claim Intake / Completeness Agent (Claims LLD Section 5, claim_intake_node).

Two deterministic checks, no LLM call — same reasoning as Phase 1's intake
agent (fast, free, fully testable without any model provider configured):

1. Documentation completeness against config.CLAIMS_REQUIRED_DOCS
2. Duplicate billing — same member+service+date_of_service as an earlier
   claim within DUPLICATE_CLAIM_WINDOW_DAYS. This is deliberately NOT the
   Fraud Agent's job (confirmed scoping decision) — it's an unambiguous rule,
   not a pattern-reasoning task.

Only claims that pass both checks are dispatched to the three specialist
agents.
"""

from src.schemas import ClaimGraphState
from src.tools.claims_db_tools import get_claim_documents, check_duplicate_claim, _connect as claims_connect
from src.audit import write_audit_entry
from src import config


def claim_intake_node(state: ClaimGraphState) -> ClaimGraphState:
    claim = state.claim

    checklist = config.CLAIMS_REQUIRED_DOCS.get(claim.service_code, [])
    attached = get_claim_documents(claim.claim_id)
    missing = [doc for doc in checklist if doc not in attached]

    duplicate_of = None
    if not missing:
        # Only check for duplicates once documentation is complete — no
        # point flagging a duplicate before we even know the claim is
        # well-formed.
        duplicate_of = check_duplicate_claim(
            claim.claim_id, claim.member_id, claim.service_code, claim.date_of_service,
        )

    write_audit_entry(
        request_id=claim.claim_id,
        step="claim_intake",
        detail={
            "service_code": claim.service_code,
            "required_docs": checklist,
            "attached_docs": attached,
            "missing_docs": missing,
            "duplicate_of_claim_id": duplicate_of,
        },
        connect_fn=claims_connect,
    )

    state.missing_documents = missing
    state.is_duplicate = duplicate_of is not None
    state.duplicate_of_claim_id = duplicate_of

    if missing:
        state.status = "pending_docs"
    elif duplicate_of:
        state.status = "duplicate"
    else:
        state.status = "dispatched"

    return state
