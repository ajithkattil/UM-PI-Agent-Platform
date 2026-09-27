"""Database tools for Phase 2 (Claims). Mirrors src/tools/db_tools.py's
three-role pattern exactly:
  - claims_agent_role  : decisioning — read claims/PA data, write its own
                          decisions + audit entries, nothing else
  - claims_intake_role : claim submission (UI) — create claims/documents,
                          cannot touch decisions or the audit log
  - pa_admin_role       : reused from Phase 1, extended with claims tables —
                          audit viewer + SIU/examiner reviewer decisions

The DB itself enforces what each role can do (scripts/setup_claims_db_roles.sql);
this module is a thin, typed wrapper around that, not the source of the
guardrail.
"""

import uuid
from datetime import date, datetime, timezone
import psycopg2
import psycopg2.extras
from src import config


def _connect(role: str = "agent"):
    creds = {
        "agent": (config.DB_CLAIMS_USER, config.DB_CLAIMS_PASSWORD),
        "intake": (config.DB_CLAIMS_INTAKE_USER, config.DB_CLAIMS_INTAKE_PASSWORD),
        "admin": (config.DB_ADMIN_USER, config.DB_ADMIN_PASSWORD),
    }[role]
    user, password = creds
    return psycopg2.connect(
        host=config.DB_HOST, dbname=config.DB_NAME, user=user, password=password,
    )


def get_claim(claim_id: str) -> dict | None:
    with _connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT claim_id, member_id, provider_id, service_code, "
            "service_description, billed_amount, date_of_service, submitted_at, "
            "status, clinical_notes FROM claims WHERE claim_id = %s",
            (claim_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_claim_documents(claim_id: str) -> list[str]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT doc_type FROM claim_documents WHERE claim_id = %s", (claim_id,),
        )
        return [row[0] for row in cur.fetchall()]


def check_duplicate_claim(claim_id: str, member_id: str, service_code: str,
                           date_of_service: date) -> str | None:
    """Returns the earlier claim_id if this claim is a duplicate (same
    member+service+date_of_service, submitted_at earlier than this claim,
    within DUPLICATE_CLAIM_WINDOW_DAYS), else None. Deterministic — this is
    the claim_intake_node's job, not the Fraud Agent's (confirmed scoping
    decision, Claims LLD Section 6)."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT c1.claim_id FROM claims c1, claims c2 "
            "WHERE c2.claim_id = %s "
            "AND c1.claim_id != c2.claim_id "
            "AND c1.member_id = %s AND c1.service_code = %s "
            "AND c1.date_of_service = %s "
            "AND c1.submitted_at < c2.submitted_at "
            "AND c2.submitted_at - c1.submitted_at <= (%s || ' days')::interval "
            "ORDER BY c1.submitted_at ASC LIMIT 1",
            (claim_id, member_id, service_code, date_of_service,
             config.DUPLICATE_CLAIM_WINDOW_DAYS),
        )
        row = cur.fetchone()
        return row[0] if row else None


def get_pa_decision_for_service(member_id: str, service_code: str) -> dict | None:
    """Reads Phase 1's pa_decisions — the literal DB-level expression of
    'Phase 2 reads Phase 1's output' from the combined architecture diagram.
    Returns the most recent decision for this member+service, or None if no
    matching PA request/decision exists at all."""
    with _connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT d.outcome, d.citation, d.confidence, d.decided_at, d.decided_by "
            "FROM pa_decisions d "
            "JOIN pa_requests r ON d.request_id = r.request_id "
            "WHERE r.member_id = %s AND r.service_code = %s "
            "ORDER BY d.decided_at DESC LIMIT 1",
            (member_id, service_code),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_earliest_pa_approval_date(member_id: str, service_code: str):
    """The EARLIEST approved decision's date for this member+service, not
    the latest. This is what a date-plausibility check should compare
    against: re-confirming an existing authorization (which happens every
    time Phase 1's eval is re-run, inserting a fresh decision row with a
    later timestamp) should never retroactively invalidate a service that
    was genuinely pre-authorized the first time. Returns None if no approved
    decision exists at all."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT MIN(d.decided_at) FROM pa_decisions d "
            "JOIN pa_requests r ON d.request_id = r.request_id "
            "WHERE r.member_id = %s AND r.service_code = %s AND d.outcome = 'approve'",
            (member_id, service_code),
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else None


def get_provider_claim_history(provider_id: str, service_code: str,
                                lookback_days: int) -> list[dict]:
    """Raw claim history for the Fraud Agent's precomputed signals
    (src/tools/fraud_signals.py) — this function returns data, it does not
    interpret it."""
    with _connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT claim_id, billed_amount, date_of_service, submitted_at "
            "FROM claims WHERE provider_id = %s AND service_code = %s "
            "AND submitted_at >= now() - (%s || ' days')::interval "
            "ORDER BY submitted_at",
            (provider_id, service_code, lookback_days),
        )
        return [dict(row) for row in cur.fetchall()]


def get_peer_claim_counts(service_code: str, lookback_days: int) -> dict[str, int]:
    """Claim count per provider for this service in the lookback window —
    the peer baseline fraud_signals.py compares a single provider against."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT provider_id, count(*) FROM claims "
            "WHERE service_code = %s AND submitted_at >= now() - (%s || ' days')::interval "
            "GROUP BY provider_id",
            (service_code, lookback_days),
        )
        return {row[0]: row[1] for row in cur.fetchall()}


def record_claim_decision(claim_id: str, decision, decided_by: str = "agent") -> str:
    """decision is a ClaimDecision (src/schemas.py) — records all three
    specialist signals individually, not just the reconciled outcome, per
    the confirmed design decision (an SIU investigator or auditor needs to
    see which agent flagged what, not just the verdict)."""
    import json
    decision_id = str(uuid.uuid4())
    role = "agent" if decided_by == "agent" else "admin"
    with _connect(role) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO claim_decisions (decision_id, claim_id, outcome, citation, "
            "coverage_signal, pa_xref_signal, fraud_signal, confidence, decided_at, decided_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (decision_id, claim_id, decision.outcome, decision.citation,
             json.dumps(decision.coverage_signal.model_dump()),
             json.dumps(decision.pa_xref_signal.model_dump()),
             json.dumps(decision.fraud_signal.model_dump()),
             decision.confidence, datetime.now(timezone.utc), decided_by),
        )
        conn.commit()
    return decision_id


# --- UI-facing (intake role) ---

def create_claim(member_id: str, provider_id: str, service_code: str,
                  service_description: str, billed_amount: float,
                  date_of_service: date, clinical_notes: str,
                  documents: list[str]) -> str:
    claim_id = str(uuid.uuid4())
    with _connect("intake") as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO claims (claim_id, member_id, provider_id, service_code, "
            "service_description, billed_amount, date_of_service, submitted_at, status, clinical_notes) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (claim_id, member_id, provider_id, service_code, service_description,
             billed_amount, date_of_service, datetime.now(timezone.utc),
             "submitted", clinical_notes),
        )
        for doc_type in documents:
            cur.execute(
                "INSERT INTO claim_documents (document_id, claim_id, doc_type, content_ref) "
                "VALUES (%s, %s, %s, %s)",
                (str(uuid.uuid4()), claim_id, doc_type, f"ui-submitted:{doc_type}"),
            )
        conn.commit()
    return claim_id


def list_claims_with_decisions() -> list[dict]:
    """Latest decision (if any) per claim, newest first — powers the
    'Claims & Decisions' tab. Mirrors list_requests_with_decisions() from
    Phase 1's db_tools.py."""
    with _connect("admin") as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT c.claim_id, c.service_code, c.service_description, c.billed_amount,
                   c.date_of_service, c.submitted_at,
                   d.outcome, d.citation, d.confidence, d.decided_by, d.decided_at
            FROM claims c
            LEFT JOIN LATERAL (
                SELECT * FROM claim_decisions
                WHERE claim_id = c.claim_id
                ORDER BY decided_at DESC LIMIT 1
            ) d ON true
            ORDER BY c.submitted_at DESC
        """)
        return [dict(row) for row in cur.fetchall()]


def list_siu_queue() -> list[dict]:
    """Claims whose latest decision is an agent-produced SIU flag — separate
    from the claims-examiner queue below on purpose (HLD Section 4.7): SIU
    is specifically for suspected fraud/waste/abuse, a merely ambiguous case
    is a different queue with a different reviewer and different stakes."""
    with _connect("admin") as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT c.claim_id, c.service_code, c.service_description, c.billed_amount,
                   c.clinical_notes, d.fraud_signal, d.confidence, d.decided_at
            FROM claims c
            JOIN LATERAL (
                SELECT fraud_signal, confidence, decided_at, outcome, decided_by
                FROM claim_decisions
                WHERE claim_id = c.claim_id
                ORDER BY decided_at DESC LIMIT 1
            ) d ON true
            WHERE d.outcome = 'flag_siu' AND d.decided_by = 'agent'
            ORDER BY d.decided_at ASC
        """)
        return [dict(row) for row in cur.fetchall()]


def list_claims_examiner_queue() -> list[dict]:
    """Claims whose latest decision is an agent-produced ordinary escalation
    (ambiguous, not fraud-suspected) — the claims-side equivalent of Phase
    1's reviewer queue."""
    with _connect("admin") as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT c.claim_id, c.service_code, c.service_description, c.billed_amount,
                   c.clinical_notes, d.coverage_signal, d.pa_xref_signal, d.fraud_signal,
                   d.confidence, d.decided_at
            FROM claims c
            JOIN LATERAL (
                SELECT coverage_signal, pa_xref_signal, fraud_signal, confidence,
                       decided_at, outcome, decided_by
                FROM claim_decisions
                WHERE claim_id = c.claim_id
                ORDER BY decided_at DESC LIMIT 1
            ) d ON true
            WHERE d.outcome = 'escalate' AND d.decided_by = 'agent'
            ORDER BY d.decided_at ASC
        """)
        return [dict(row) for row in cur.fetchall()]


def record_claim_reviewer_decision(claim_id: str, outcome: str, citation: str, reviewer_id: str) -> str:
    """A human (SIU investigator or claims examiner) resolving a flagged/
    escalated claim — written by the admin role, never the agent role, same
    separation as Phase 1's record_reviewer_decision."""
    import json
    from src.schemas import SpecialistSignal
    empty_signal = SpecialistSignal(agent_name="coverage", flagged_outcome="n/a",
                                     confidence=1.0, reasoning_summary="reviewer decision, no agent signal")
    decision_id = str(uuid.uuid4())
    with _connect("admin") as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO claim_decisions (decision_id, claim_id, outcome, citation, "
            "coverage_signal, pa_xref_signal, fraud_signal, confidence, decided_at, decided_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (decision_id, claim_id, outcome, citation,
             json.dumps(empty_signal.model_dump()), json.dumps(empty_signal.model_dump()),
             json.dumps(empty_signal.model_dump()), 1.0, datetime.now(timezone.utc), reviewer_id),
        )
        conn.commit()
    return decision_id
