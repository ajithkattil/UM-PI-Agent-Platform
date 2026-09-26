"""Database tools available to the agent and to the UI.

Three connection roles, matched to who's actually doing the action — never an
admin/superuser connection string from application code:
  - pa_agent_role  : the decision-making agent (read requests, write its own
                      decisions + audit entries, nothing else)
  - pa_intake_role : request submission (what the UI uses when a provider
                      submits a new PA request) — can create requests and
                      documents, cannot touch decisions or the audit log
  - pa_admin_role  : audit viewer + reviewer decisions (a human resolving an
                      escalated case) — read everything, write only decisions

The DB itself enforces what each role can do (see scripts/setup_db_roles.sql);
this module is a thin, typed wrapper around that, not the source of the
guardrail.
"""

import uuid
from datetime import datetime, timezone
import psycopg2
import psycopg2.extras
from src import config


def _connect(role: str = "agent"):
    creds = {
        "agent": (config.DB_USER, config.DB_PASSWORD),
        "intake": (config.DB_INTAKE_USER, config.DB_INTAKE_PASSWORD),
        "admin": (config.DB_ADMIN_USER, config.DB_ADMIN_PASSWORD),
    }[role]
    user, password = creds
    return psycopg2.connect(
        host=config.DB_HOST, dbname=config.DB_NAME, user=user, password=password,
    )


def get_request(request_id: str) -> dict | None:
    with _connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT request_id, member_id, provider_id, service_code, "
            "service_description, submitted_at, status, request_type, clinical_notes "
            "FROM pa_requests WHERE request_id = %s",
            (request_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_request_documents(request_id: str) -> list[str]:
    """Returns the list of doc_type values actually attached to a request —
    the intake agent checks this against config.REQUIRED_DOCS rather than
    trusting whatever the caller claims was submitted."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT doc_type FROM pa_request_documents WHERE request_id = %s",
            (request_id,),
        )
        return [row[0] for row in cur.fetchall()]


def get_member_plan(member_id: str) -> dict | None:
    with _connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT m.member_id, m.date_of_birth, m.gender, p.plan_name, p.plan_year "
            "FROM members m JOIN plans p ON m.plan_id = p.plan_id "
            "WHERE m.member_id = %s",
            (member_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_prior_requests_for_member(member_id: str) -> list[dict]:
    """Not used by the Decision Agent in Phase 1 — reserved for the
    Fraud/Abuse pattern agent extension discussed separately (LLD Section 6)."""
    with _connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT request_id, service_code, submitted_at, status "
            "FROM pa_requests WHERE member_id = %s ORDER BY submitted_at DESC",
            (member_id,),
        )
        return [dict(row) for row in cur.fetchall()]


def record_decision(request_id: str, outcome: str, citation: str | None,
                     confidence: float, decided_by: str = "agent",
                     role: str = "agent", decided_at: datetime | None = None) -> str:
    decision_id = str(uuid.uuid4())
    with _connect(role) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO pa_decisions "
            "(decision_id, request_id, outcome, citation, confidence, decided_at, decided_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (decision_id, request_id, outcome, citation, confidence,
             decided_at or datetime.now(timezone.utc), decided_by),
        )
        conn.commit()
    return decision_id


# --- UI-facing functions (intake + admin roles) ---

def list_members() -> list[dict]:
    with _connect("intake") as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT m.member_id, m.date_of_birth, p.plan_name "
            "FROM members m JOIN plans p ON m.plan_id = p.plan_id ORDER BY m.member_id"
        )
        return [dict(row) for row in cur.fetchall()]


def list_providers() -> list[dict]:
    with _connect("intake") as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT provider_id, provider_name, npi FROM providers ORDER BY provider_name")
        return [dict(row) for row in cur.fetchall()]


def create_pa_request(member_id: str, provider_id: str, service_code: str,
                       service_description: str, request_type: str,
                       clinical_notes: str, documents: list[str]) -> str:
    """Creates a new PA request as the intake role — the role that submits
    requests is deliberately not the role that decides them (see
    scripts/setup_db_roles.sql)."""
    request_id = str(uuid.uuid4())
    with _connect("intake") as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO pa_requests (request_id, member_id, provider_id, service_code, "
            "service_description, submitted_at, status, request_type, clinical_notes) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (request_id, member_id, provider_id, service_code, service_description,
             datetime.now(timezone.utc), "submitted", request_type, clinical_notes),
        )
        for doc_type in documents:
            cur.execute(
                "INSERT INTO pa_request_documents (document_id, request_id, doc_type, content_ref) "
                "VALUES (%s, %s, %s, %s)",
                (str(uuid.uuid4()), request_id, doc_type, f"ui-submitted:{doc_type}"),
            )
        conn.commit()
    return request_id


def list_requests_with_decisions() -> list[dict]:
    """Latest decision (if any) per request, newest requests first — powers
    the 'Requests & Decisions' tab."""
    with _connect("admin") as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT r.request_id, r.service_code, r.service_description,
                   r.request_type, r.submitted_at,
                   d.outcome, d.citation, d.confidence, d.decided_by, d.decided_at
            FROM pa_requests r
            LEFT JOIN LATERAL (
                SELECT * FROM pa_decisions
                WHERE request_id = r.request_id
                ORDER BY decided_at DESC LIMIT 1
            ) d ON true
            ORDER BY r.submitted_at DESC
        """)
        return [dict(row) for row in cur.fetchall()]


def list_escalated_awaiting_review() -> list[dict]:
    """Requests whose latest decision is an agent-produced escalation — once
    a human reviewer records a decision, that becomes the new latest and the
    request naturally drops off this list."""
    with _connect("admin") as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT r.request_id, r.service_code, r.service_description,
                   r.clinical_notes, d.citation, d.confidence, d.decided_at
            FROM pa_requests r
            JOIN LATERAL (
                SELECT citation, confidence, decided_at, outcome, decided_by
                FROM pa_decisions
                WHERE request_id = r.request_id
                ORDER BY decided_at DESC LIMIT 1
            ) d ON true
            WHERE d.outcome = 'escalate' AND d.decided_by = 'agent'
            ORDER BY d.decided_at ASC
        """)
        return [dict(row) for row in cur.fetchall()]


def record_reviewer_decision(request_id: str, outcome: str, citation: str, reviewer_id: str) -> str:
    """A human reviewer resolving an escalated case — written by the admin
    role, never the agent role, so 'agent decided' vs 'person decided' stays
    distinguishable by decided_by AND by which DB credential actually wrote
    the row (not just the string value — that's what makes this a real
    separation, not a label)."""
    return record_decision(request_id, outcome, citation, confidence=1.0,
                            decided_by=reviewer_id, role="admin")
