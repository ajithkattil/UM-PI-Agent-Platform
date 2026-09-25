"""Intake / Completeness Agent (LLD Section 4, intake_node).

Deliberately not an LLM call — documentation completeness against a known
checklist is a deterministic lookup, and keeping it that way means it's fast,
free, and fully testable without any model provider configured. The Decision
Agent (which does need the LLM) only ever sees requests that pass this gate.
"""

from src.schemas import GraphState
from src.tools.db_tools import get_request_documents
from src.audit import write_audit_entry
from src import config


def intake_node(state: GraphState) -> GraphState:
    request = state.request

    checklist = config.REQUIRED_DOCS.get(request.service_code, {}).get(
        request.request_type, []
    )

    attached = get_request_documents(request.request_id)
    missing = [doc for doc in checklist if doc not in attached]

    write_audit_entry(
        request_id=request.request_id,
        step="intake",
        detail={
            "service_code": request.service_code,
            "request_type": request.request_type,
            "required_docs": checklist,
            "attached_docs": attached,
            "missing_docs": missing,
        },
    )

    state.missing_documents = missing
    state.status = "pending_docs" if missing else "policy_retrieval"
    return state
