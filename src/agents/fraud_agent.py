"""Fraud / Anomaly Pattern Agent (Claims HLD Section 4.5 / LLD Section 5 & 7,
fraud_node).

Two-stage design, same reasoning as the rest of this project's split between
deterministic fact-gathering and LLM judgment: fraud_signals.compute_signals()
computes real numbers (claim count vs. peer median, threshold-clustering) —
this agent's LLM call reasons over those numbers into a flagged/clear
judgment with a citation, but never sees or reasons over raw claim history
itself. That's what makes "flagged" mean something specific and auditable
("10x the peer median for this service") rather than a vague, unfalsifiable
"this looks suspicious."

Same hard constraint as coverage_agent.py, applied to this agent's own
version of an accusatory outcome: a "flagged" signal without a citation is
invalid — retry once, then force low confidence rather than ever letting an
uncited fraud flag through reconciliation.
"""

import json
from typing import Callable
from src.schemas import ClaimGraphState, SpecialistSignal
from src.audit import write_audit_entry
from src.tools.claims_db_tools import _connect as claims_connect
from src.tools.fraud_signals import compute_signals
from src import config

SYSTEM_INSTRUCTIONS = """You are assessing a health insurance claim for
potential fraud, waste, or abuse (FWA) risk, based STRICTLY on the
precomputed metrics provided below — you have not been given and must not
speculate about this provider's raw claim history beyond these numbers.

The "Volume anomaly flag" below is not just a data point for you to weigh —
it is the definitive answer to "does this provider's volume exceed the
configured threshold." If it says True, the metrics DO genuinely support a
flag; do not re-derive or second-guess that conclusion by reasoning about
whether the ratio "feels" large enough. Treat volume_anomaly=True as
sufficient grounds to flag on its own, unless something else in the metrics
specifically contradicts it (there is nothing else here that would).
The caution below is about not flagging on a WEAK or ambiguous reading of
the numbers — it does not mean discount a clear True flag out of general
caution.

If you flag this as a concern, you must cite the SPECIFIC metric that
justifies it — e.g. "provider billed 10.0x the peer median for this service
in the last 90 days" — not a vague "unusual pattern." A flag with no
specific metric cited is not a valid response.

A flag is a serious action with real provider-relations cost — only flag
when the computed metrics genuinely support it (volume_anomaly is true, or
threshold_clustering_count is meaningfully elevated). When volume_anomaly is
False and threshold_clustering_count is 0, there is no basis to flag —
output "clear". When genuinely unsure because the numbers themselves are
ambiguous (not because flagging feels uncomfortable), output "clear" with a
lower confidence value rather than flagging speculatively — a downstream
reconciliation step decides whether the case still needs human review based
on your confidence.

Respond with a single JSON object matching this schema exactly, and nothing else:
{"outcome": "clear" | "flagged",
 "citation": "<specific metric cited, or null>",
 "confidence": <float 0.0-1.0>,
 "reasoning_summary": "<one or two sentences>"}
"""


def build_prompt(computed_metrics: dict) -> str:
    return (
        f"{SYSTEM_INSTRUCTIONS}\n\n"
        f"--- Computed Metrics (last {computed_metrics['lookback_days']} days) ---\n"
        f"This provider's claim count for this service: {computed_metrics['provider_claim_count']}\n"
        f"Peer median claim count for this service: {computed_metrics['peer_median_claim_count']}\n"
        f"Volume ratio (this provider vs. peer median): "
        f"{computed_metrics['volume_ratio']}\n"
        f"Volume anomaly flag (>= {config.FRAUD_VOLUME_THRESHOLD_MULTIPLIER}x peer median): "
        f"{computed_metrics['volume_anomaly']}\n"
        f"Claims clustered near a known review threshold: "
        f"{computed_metrics['threshold_clustering_count']}\n"
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


def _parse_signal(raw: str, computed_metrics: dict) -> SpecialistSignal:
    try:
        data = json.loads(raw)
        return SpecialistSignal(
            agent_name="fraud",
            flagged_outcome=data["outcome"],
            citation=data.get("citation"),
            confidence=data["confidence"],
            reasoning_summary=data["reasoning_summary"],
            computed_metrics=computed_metrics,
        )
    except (json.JSONDecodeError, TypeError, ValueError, KeyError):
        return SpecialistSignal(
            agent_name="fraud", flagged_outcome="unresolved", citation=None,
            confidence=0.0, reasoning_summary="Model output could not be parsed as a valid signal.",
            computed_metrics=computed_metrics,
        )


def _citation_valid(signal: SpecialistSignal) -> bool:
    if signal.flagged_outcome == "flagged" and not signal.citation:
        return False
    return True


def fraud_node(state: ClaimGraphState, llm_call_fn: Callable[[str], str] = _default_llm_call,
               compute_signals_fn: Callable = compute_signals) -> ClaimGraphState:
    claim = state.claim
    computed_metrics = compute_signals_fn(claim.provider_id, claim.service_code)
    prompt = build_prompt(computed_metrics)

    retried = False
    try:
        raw = llm_call_fn(prompt)
        signal = _parse_signal(raw, computed_metrics)
    except Exception as e:
        signal = SpecialistSignal(
            agent_name="fraud", flagged_outcome="unresolved", citation=None,
            confidence=0.0, reasoning_summary=f"Model call failed rather than producing a usable response: {e}",
            computed_metrics=computed_metrics,
        )

    if signal.flagged_outcome == "flagged" and not _citation_valid(signal):
        retried = True
        correction_prompt = prompt + (
            "\n\nYour previous response flagged this claim without citing a "
            "specific metric. A flag must cite a specific number from the "
            "metrics above. Respond again."
        )
        try:
            raw = llm_call_fn(correction_prompt)
            signal = _parse_signal(raw, computed_metrics)
        except Exception:
            signal = SpecialistSignal(
                agent_name="fraud", flagged_outcome="unresolved", citation=None,
                confidence=0.0, reasoning_summary="Model call failed on the citation-retry attempt.",
                computed_metrics=computed_metrics,
            )

        if signal.flagged_outcome == "flagged" and not _citation_valid(signal):
            signal = SpecialistSignal(
                agent_name="fraud", flagged_outcome="unresolved", citation=None, confidence=0.0,
                reasoning_summary="Model produced an uncited fraud flag twice; forcing low confidence rather than letting an invalid flag through.",
                computed_metrics=computed_metrics,
            )

    write_audit_entry(
        request_id=claim.claim_id,
        step="fraud_agent",
        detail={
            "flagged_outcome": signal.flagged_outcome,
            "citation": signal.citation,
            "confidence": signal.confidence,
            "reasoning_summary": signal.reasoning_summary,
            "retried_for_citation": retried,
            "computed_metrics": computed_metrics,
        },
        connect_fn=claims_connect,
    )

    state.fraud_signal = signal
    return state
