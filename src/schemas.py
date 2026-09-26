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
    clinical_notes: str = ""  # the actual clinical content the Decision Agent
    # reasons over — service_code/documents alone don't tell it anything
    # case-specific; this was the missing piece that made eval catch two
    # different scenarios producing identical decisions.


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


# ============================================================
# Phase 2 — Claims / Payment Integrity
# ============================================================

class ClaimRequest(BaseModel):
    claim_id: str
    member_id: str
    provider_id: str
    service_code: str
    service_description: str
    billed_amount: float
    date_of_service: date
    documents: list[str] = []
    clinical_notes: str = ""


class SpecialistSignal(BaseModel):
    """Common shape all three specialist agents produce — reconcile()
    consumes these structured signals, never raw agent text, which is what
    makes the reconciliation logic deterministic and independently testable
    with scripted fake signals (same trick as Phase 1's
    test_orchestrator_routing.py)."""
    agent_name: Literal["coverage", "pa_xref", "fraud"]
    flagged_outcome: str
    # coverage: "approve" | "deny"
    # pa_xref: "matches" | "missing" | "denied" (no PA required -> matches)
    # fraud: "clear" | "flagged"
    citation: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_summary: str
    computed_metrics: dict = {}  # only the fraud agent populates this


class ClaimDecision(BaseModel):
    outcome: Literal["pay", "deny", "flag_siu", "escalate"]
    citation: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    coverage_signal: SpecialistSignal
    pa_xref_signal: SpecialistSignal
    fraud_signal: SpecialistSignal

    def validate_citation_rule(self) -> bool:
        # Same hard constraint as Phase 1: a deny without a citation is
        # invalid output.
        if self.outcome == "deny" and not self.citation:
            return False
        return True


class ClaimGraphState(BaseModel):
    claim: ClaimRequest
    missing_documents: list[str] = []
    is_duplicate: bool = False
    duplicate_of_claim_id: Optional[str] = None
    retrieved_policy: list[PolicyChunk] = []
    coverage_signal: Optional[SpecialistSignal] = None
    pa_xref_signal: Optional[SpecialistSignal] = None
    fraud_signal: Optional[SpecialistSignal] = None
    decision: Optional[ClaimDecision] = None
    status: Literal["intake", "pending_docs", "duplicate", "dispatched", "decided"] = "intake"
