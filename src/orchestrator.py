"""Orchestrator — the linear graph locked in for this phase (HLD Section 5):
intake -> [complete?] -> policy_retrieval -> decision -> [escalate?] -> end.

A supervisor / multi-agent structure was deliberately deferred — see the
Coverage/Clinical-Necessity split and Fraud/Abuse agent discussed separately
as the next extension, once this linear version is proven end-to-end.
"""

from langgraph.graph import StateGraph, END
from src.schemas import GraphState
from src.agents.intake_agent import intake_node
from src.agents.decision_agent import decision_node
from src.tools.policy_tools import retrieve_policy
from src.audit import write_audit_entry
from datetime import datetime, timezone


def policy_retrieval_node(state: GraphState) -> GraphState:
    request = state.request
    chunks = retrieve_policy(
        service_code=request.service_code,
        request_date=datetime.now(timezone.utc).date(),
    )
    write_audit_entry(
        request_id=request.request_id,
        step="policy_retrieval",
        detail={
            "service_code": request.service_code,
            "chunks_retrieved": len(chunks),
            "sources": [c.source_file for c in chunks],
        },
    )
    state.retrieved_policy = chunks
    return state


def escalation_node(state: GraphState) -> GraphState:
    write_audit_entry(
        request_id=state.request.request_id,
        step="escalation",
        detail={
            "reasoning_summary": state.decision.reasoning_summary if state.decision else None,
            "confidence": state.decision.confidence if state.decision else None,
        },
    )
    return state


def route_after_intake(state: GraphState) -> str:
    return "policy_retrieval" if not state.missing_documents else END


def route_after_decision(state: GraphState) -> str:
    return "escalation" if state.decision and state.decision.outcome == "escalate" else END


def build_graph(decision_node_fn=decision_node, policy_retrieval_node_fn=policy_retrieval_node):
    """Both node functions are injectable so tests can run the full graph
    wiring with a scripted fake model and a stubbed retrieval step, without
    needing a real LLM or Pinecone — see tests/test_orchestrator_routing.py."""
    graph = StateGraph(GraphState)

    graph.add_node("intake", intake_node)
    graph.add_node("policy_retrieval", policy_retrieval_node_fn)
    graph.add_node("decision", decision_node_fn)
    graph.add_node("escalation", escalation_node)

    graph.set_entry_point("intake")
    graph.add_conditional_edges("intake", route_after_intake, {
        "policy_retrieval": "policy_retrieval", END: END,
    })
    graph.add_edge("policy_retrieval", "decision")
    graph.add_conditional_edges("decision", route_after_decision, {
        "escalation": "escalation", END: END,
    })
    graph.add_edge("escalation", END)

    return graph.compile()
