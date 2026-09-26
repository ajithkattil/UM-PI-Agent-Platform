"""Claims Orchestrator (Claims HLD Section 6 / LLD Section 5).

intake -> [complete? not duplicate?] -> dispatch -> {coverage, pa_xref, fraud}
       (parallel, none depends on another's output) -> reconcile -> end

This is the first genuinely parallel fan-out/join in this project — Phase
1's graph was purely linear. The three specialist nodes run off the same
post-intake state and none needs another's output; only reconcile needs all
three.
"""

from langgraph.graph import StateGraph, END
from src.schemas import ClaimGraphState
from src.agents.claim_intake_agent import claim_intake_node
from src.agents.coverage_agent import coverage_node
from src.agents.pa_xref_agent import pa_xref_node
from src.agents.fraud_agent import fraud_node
from src.agents.reconciliation import reconcile
from src.tools.claims_db_tools import record_claim_decision
from src.audit import write_audit_entry
from src.tools.claims_db_tools import _connect as claims_connect


def dispatch_node(state: ClaimGraphState) -> ClaimGraphState:
    """Pure pass-through — exists only as a fan-out point so claim_intake's
    conditional routing has a single 'proceed' target that then fans out to
    all three specialists unconditionally."""
    return state


def reconcile_node(state: ClaimGraphState) -> ClaimGraphState:
    decision = reconcile(state.coverage_signal, state.pa_xref_signal, state.fraud_signal)
    record_claim_decision(state.claim.claim_id, decision, decided_by="agent")
    write_audit_entry(
        request_id=state.claim.claim_id,
        step="reconcile",
        detail={
            "outcome": decision.outcome,
            "citation": decision.citation,
            "confidence": decision.confidence,
            "coverage_flagged_outcome": state.coverage_signal.flagged_outcome,
            "pa_xref_flagged_outcome": state.pa_xref_signal.flagged_outcome,
            "fraud_flagged_outcome": state.fraud_signal.flagged_outcome,
        },
        connect_fn=claims_connect,
    )
    state.decision = decision
    state.status = "decided"
    return state


def route_after_intake(state: ClaimGraphState) -> str:
    if state.status == "pending_docs":
        return END
    if state.status == "duplicate":
        return END
    return "dispatch"


def build_claims_graph(coverage_node_fn=coverage_node, pa_xref_node_fn=pa_xref_node,
                        fraud_node_fn=fraud_node):
    """Node functions are injectable so tests can run the full graph with
    scripted fake signals, same pattern as Phase 1's build_graph(). Each is
    still expected to take and return a full ClaimGraphState (so they stay
    independently testable exactly as in test_coverage_agent.py /
    test_pa_xref_agent.py / test_fraud_agent.py) — the wrappers below are
    what adapt that into the partial-update dict LangGraph's parallel fan-out
    actually requires, so this constraint lives in exactly one place rather
    than leaking into every agent's own contract."""

    def _coverage_wrapper(state):
        result = coverage_node_fn(state)
        return {"coverage_signal": result.coverage_signal, "retrieved_policy": result.retrieved_policy}

    def _pa_xref_wrapper(state):
        result = pa_xref_node_fn(state)
        return {"pa_xref_signal": result.pa_xref_signal}

    def _fraud_wrapper(state):
        result = fraud_node_fn(state)
        return {"fraud_signal": result.fraud_signal}

    graph = StateGraph(ClaimGraphState)

    graph.add_node("claim_intake", claim_intake_node)
    graph.add_node("dispatch", dispatch_node)
    graph.add_node("coverage", _coverage_wrapper)
    graph.add_node("pa_xref", _pa_xref_wrapper)
    graph.add_node("fraud", _fraud_wrapper)
    graph.add_node("reconcile", reconcile_node)

    graph.set_entry_point("claim_intake")
    graph.add_conditional_edges("claim_intake", route_after_intake, {
        "dispatch": "dispatch", END: END,
    })
    graph.add_edge("dispatch", "coverage")
    graph.add_edge("dispatch", "pa_xref")
    graph.add_edge("dispatch", "fraud")
    # Join: reconcile must wait for ALL THREE specialists, not run once per
    # predecessor — LangGraph's list-form add_edge is what makes this a true
    # join rather than three separate triggers. Verified empirically in
    # tests/test_claims_orchestrator.py, not just assumed.
    graph.add_edge(["coverage", "pa_xref", "fraud"], "reconcile")
    graph.add_edge("reconcile", END)

    return graph.compile()
