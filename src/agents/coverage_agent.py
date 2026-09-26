"""Coverage / Coding Agent (Claims HLD Section 4.3 / LLD Section 5,
coverage_node).

Deliberately reuses Phase 1's decision_agent.py pattern near-verbatim: same
fixed-section prompt structure, same hard citation-validation/retry rule, same
escalate-on-failure robustness (the ThinkingBlock-only-response and
malformed-JSON fixes Phase 1 needed for real — applied here from the start
rather than waiting to hit them again).

One real difference from Phase 1: this agent produces a SpecialistSignal
(approve/deny only), never "escalate" itself. Escalation is a reconciliation
decision across all three specialist signals, not a per-agent one — if this
agent can't produce valid output, it reports confidence=0.0, which forces
escalation through reconcile()'s confidence check without this agent needing
its own escalate branch.
"""

import json
from typing import Callable
from src.schemas import ClaimGraphState, ClaimRequest, PolicyChunk, SpecialistSignal
from src.audit import write_audit_entry
from src.tools.claims_db_tools import _connect as claims_connect
from src.tools.policy_tools import retrieve_policy
from src import config

SYSTEM_INSTRUCTIONS = """You are deciding whether to PAY or DENY a submitted
health insurance claim, based strictly on the policy text provided below —
never from general medical knowledge. This is a post-service claim: the
service has already been delivered: your job is to determine whether it met
coverage/medical-necessity criteria, not whether to authorize it in advance.

If you deny, you must cite the specific criterion or requirement that was not
met — not just a section header. "Coverage Criteria" alone is not an
acceptable citation; "Coverage Criteria, item 2 (6-week conservative therapy
trial not documented)" is.

You do not have an "escalate" option — if the provided policy chunks do not
clearly resolve this claim, output your best judgment as approve or deny but
set a LOW confidence value; a downstream reconciliation step (not you)
decides whether the case needs human review.

Respond with a single JSON object matching this schema exactly, and nothing else:
{"outcome": "approve" | "deny",
 "citation": "<specific policy section AND criterion cited, or null>",
 "confidence": <float 0.0-1.0>,
 "reasoning_summary": "<one or two sentences>"}
"""


def build_prompt(claim: ClaimRequest, retrieved_policy: list[PolicyChunk]) -> str:
    policy_text = "\n\n".join(
        f"[Source: {c.source_file} / Section: {c.section_header}]\n{c.text}"
        for c in retrieved_policy
    )
    return (
        f"{SYSTEM_INSTRUCTIONS}\n\n"
        f"--- Claim ---\n"
        f"Service: {claim.service_description} ({claim.service_code})\n"
        f"Billed amount: ${claim.billed_amount:.2f}\n"
        f"Date of service: {claim.date_of_service}\n"
        f"Documents on file: {', '.join(claim.documents) or 'none'}\n"
        f"Clinical notes: {claim.clinical_notes or '(none provided)'}\n\n"
        f"--- Retrieved Policy ---\n{policy_text}\n"
    )


def _default_llm_call(prompt: str) -> str:
    if config.MODEL_PROVIDER == "ANTHROPIC":
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=config.MODEL_NAME,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if getattr(block, "type", None) == "text":
                return block.text
        raise ValueError(f"No text block found in model response: {response.content!r}")
    raise NotImplementedError(f"Model provider {config.MODEL_PROVIDER} not wired up yet")


def _parse_signal(raw: str) -> SpecialistSignal:
    try:
        data = json.loads(raw)
        return SpecialistSignal(
            agent_name="coverage",
            flagged_outcome=data["outcome"],
            citation=data.get("citation"),
            confidence=data["confidence"],
            reasoning_summary=data["reasoning_summary"],
        )
    except (json.JSONDecodeError, TypeError, ValueError, KeyError):
        return SpecialistSignal(
            agent_name="coverage", flagged_outcome="unresolved", citation=None,
            confidence=0.0, reasoning_summary="Model output could not be parsed as a valid signal.",
        )


def _citation_valid(signal: SpecialistSignal) -> bool:
    # Same hard constraint as Phase 1: a deny without a citation is invalid.
    if signal.flagged_outcome == "deny" and not signal.citation:
        return False
    return True


def coverage_node(state: ClaimGraphState, llm_call_fn: Callable[[str], str] = _default_llm_call,
                   retrieve_policy_fn: Callable = retrieve_policy) -> ClaimGraphState:
    claim = state.claim

    retrieved = retrieve_policy_fn(service_code=claim.service_code, request_date=claim.date_of_service)
    state.retrieved_policy = retrieved

    prompt = build_prompt(claim, retrieved)

    retried = False
    try:
        raw = llm_call_fn(prompt)
        signal = _parse_signal(raw)
    except Exception as e:
        signal = SpecialistSignal(
            agent_name="coverage", flagged_outcome="unresolved", citation=None,
            confidence=0.0, reasoning_summary=f"Model call failed rather than producing a usable response: {e}",
        )

    if signal.flagged_outcome == "deny" and not _citation_valid(signal):
        retried = True
        correction_prompt = prompt + (
            "\n\nYour previous response denied this claim without a citation. "
            "A denial must cite a specific policy criterion. Respond again."
        )
        try:
            raw = llm_call_fn(correction_prompt)
            signal = _parse_signal(raw)
        except Exception:
            signal = SpecialistSignal(
                agent_name="coverage", flagged_outcome="unresolved", citation=None,
                confidence=0.0, reasoning_summary="Model call failed on the citation-retry attempt.",
            )

        if signal.flagged_outcome == "deny" and not _citation_valid(signal):
            signal = SpecialistSignal(
                agent_name="coverage", flagged_outcome="unresolved", citation=None, confidence=0.0,
                reasoning_summary="Model produced an uncited denial twice; forcing low confidence rather than letting an invalid decision through.",
            )

    write_audit_entry(
        request_id=claim.claim_id,
        step="coverage_agent",
        detail={
            "flagged_outcome": signal.flagged_outcome,
            "citation": signal.citation,
            "confidence": signal.confidence,
            "reasoning_summary": signal.reasoning_summary,
            "retried_for_citation": retried,
            "policy_chunks_used": len(retrieved),
        },
        connect_fn=claims_connect,
    )

    state.coverage_signal = signal
    return state
