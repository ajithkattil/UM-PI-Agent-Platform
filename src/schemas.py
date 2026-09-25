"""Core data models — matches LLD Section 3."""

from pydantic import BaseModel, Field
from typing import Literal, Optional
from datetime import date


class PARequest(BaseModel):
    request_id: str
    member_id: str
    provider_id: str
    service_code: str
    service_description: str
    request_type: Literal["standard", "expedited"]
    documents: list[str] = []


class PolicyChunk(BaseModel):
    text: str
    source_file: str
    section_header: Optional[str] = None
    effective_date: Optional[date] = None


class Decision(BaseModel):
    outcome: Literal["approve", "deny", "escalate"]
    citation: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_summary: str

    def validate_citation_rule(self) -> bool:
        """Hard constraint from HLD 3.3 — a deny without a citation is invalid
        output. Enforced here, not just in the prompt, so a model that ignores
        the instruction can be caught programmatically rather than silently
        producing an uncited denial."""
        if self.outcome == "deny" and not self.citation:
            return False
        return True


class GraphState(BaseModel):
    request: PARequest
    missing_documents: list[str] = []
    retrieved_policy: list[PolicyChunk] = []
    decision: Optional[Decision] = None
    status: Literal["intake", "pending_docs", "policy_retrieval", "decided"] = "intake"
