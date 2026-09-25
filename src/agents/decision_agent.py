"""Decision Agent (LLD Sections 4 & 8).

The LLM call itself is injected (`llm_call_fn`) rather than hardcoded, for two
reasons: it's what makes the model-gateway swap (LLD Section 5 / config.py)
just a config change, and it's what makes the citation-validation/retry logic
below testable without any real API credentials — see
tests/test_orchestrator_routing.py, which exercises this exact function with a
scripted fake model.
"""

import json
from typing import Callable
from src.schemas import GraphState, Decision, PARequest, PolicyChunk
from src.audit import write_audit_entry
from src import config

# Fixed prompt sections per LLD Section 8 — not free-form.
SYSTEM_INSTRUCTIONS = """You are deciding a prior authorization request for a health
payer. You must decide strictly from the policy text provided below — never from
general medical knowledge. If the provided policy chunks do not clearly resolve
whether this request should be approved or denied, you must output "escalate"
rather than guess. If you deny, you must cite the specific policy section your
denial is based on; a denial with no citation is not a valid response.

Respond with a single JSON object matching this schema exactly, and nothing else:
{"outcome": "approve" | "deny" | "escalate",
 "citation": "<policy section cited, or null>",
 "confidence": <float 0.0-1.0>,
 "reasoning_summary": "<one or two sentences>"}
"""


def build_prompt(request: PARequest, retrieved_policy: list[PolicyChunk]) -> str:
    policy_text = "\n\n".join(
        f"[Source: {c.source_file} / Section: {c.section_header}]\n{c.text}"
        for c in retrieved_policy
    )
    return (
        f"{SYSTEM_INSTRUCTIONS}\n\n"
        f"--- Request ---\n"
        f"Service: {request.service_description} ({request.service_code})\n"
        f"Request type: {request.request_type}\n\n"
        f"--- Retrieved Policy ---\n{policy_text}\n"
    )


def _default_llm_call(prompt: str) -> str:
    """Real path — calls the configured model provider. Not exercised in this
    environment (no API credentials available); see config.MODEL_PROVIDER for
    the gateway switch this routes through."""
    if config.MODEL_PROVIDER == "ANTHROPIC":
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=config.MODEL_NAME,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    raise NotImplementedError(f"Model provider {config.MODEL_PROVIDER} not wired up yet")


def decision_node(state: GraphState, llm_call_fn: Callable[[str], str] = _default_llm_call) -> GraphState:
    request = state.request
    prompt = build_prompt(request, state.retrieved_policy)

    raw = llm_call_fn(prompt)
    decision = _parse_decision(raw)

    # Hard constraint: a deny without a citation is invalid output. Retry once
    # with an explicit correction, then hard-escalate rather than ever letting
    # an uncited denial through — this is enforced here, not just requested in
    # the prompt.
    retried = False
    if decision.outcome == "deny" and not decision.validate_citation_rule():
        retried = True
        correction_prompt = prompt + (
            "\n\nYour previous response denied this request without a citation. "
            "A denial must cite a specific policy section. Respond again."
        )
        raw = llm_call_fn(correction_prompt)
        decision = _parse_decision(raw)

        if decision.outcome == "deny" and not decision.validate_citation_rule():
            decision = Decision(
                outcome="escalate",
                citation=None,
                confidence=0.0,
                reasoning_summary="Model produced an uncited denial twice; escalated rather than let an invalid decision through.",
            )

    # Confidence-gated escalation (HLD 3.3 / LLD 5) — a stated approve/deny
    # below threshold is overridden to escalate regardless of what the model
    # claimed its outcome was.
    if decision.outcome != "escalate" and decision.confidence < config.ESCALATION_THRESHOLD:
        decision = decision.model_copy(update={"outcome": "escalate"})

    write_audit_entry(
        request_id=request.request_id,
        step="decision",
        detail={
            "outcome": decision.outcome,
            "citation": decision.citation,
            "confidence": decision.confidence,
            "reasoning_summary": decision.reasoning_summary,
            "retried_for_citation": retried,
            "policy_chunks_used": len(state.retrieved_policy),
        },
    )

    state.decision = decision
    state.status = "decided"
    return state


def _parse_decision(raw: str) -> Decision:
    try:
        data = json.loads(raw)
        return Decision(**data)
    except (json.JSONDecodeError, TypeError, ValueError):
        # Malformed model output is treated as a reason to escalate, never as
        # a reason to guess or crash the pipeline.
        return Decision(
            outcome="escalate",
            citation=None,
            confidence=0.0,
            reasoning_summary="Model output could not be parsed as a valid decision.",
        )
