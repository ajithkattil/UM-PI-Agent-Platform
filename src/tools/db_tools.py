"""Database tools available to the agent.

Every connection here uses the restricted `pa_agent_role` credentials from
config.py — never an admin connection string, even in local dev. The DB itself
enforces what these functions are allowed to do (see scripts/setup_db_roles.sql);
this module is a thin, typed wrapper around that, not the source of the
guardrail.
"""

import psycopg2
import psycopg2.extras
from src import config


def _connect():
    return psycopg2.connect(
        host=config.DB_HOST,
        dbname=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
    )


def get_request(request_id: str) -> dict | None:
    with _connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT request_id, member_id, provider_id, service_code, "
            "service_description, submitted_at, status, request_type "
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
                     confidence: float, decided_by: str = "agent") -> str:
    import uuid
    from datetime import datetime, timezone

    decision_id = str(uuid.uuid4())
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO pa_decisions "
            "(decision_id, request_id, outcome, citation, confidence, decided_at, decided_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (decision_id, request_id, outcome, citation, confidence,
             datetime.now(timezone.utc), decided_by),
        )
        conn.commit()
    return decision_id
