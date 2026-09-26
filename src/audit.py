"""Audit logging. Every node writes one row before returning — inline, not a
separate pass at the end — so a crash mid-graph still leaves a trail (LLD
Section 4). Both pa_agent_role (Phase 1) and claims_agent_role (Phase 2) have
INSERT-only access here; see setup_db_roles.sql / setup_claims_db_roles.sql.

connect_fn lets each phase use its own role's credentials rather than this
module hardcoding Phase 1's — defaults to Phase 1's for backward
compatibility with existing callers."""

import json


def write_audit_entry(request_id: str, step: str, detail: dict, connect_fn=None) -> None:
    if connect_fn is None:
        from src.tools.db_tools import _connect as connect_fn
    with connect_fn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO audit_log (request_id, step, detail) VALUES (%s, %s, %s)",
            (request_id, step, json.dumps(detail)),
        )
        conn.commit()


def get_audit_trail(request_id: str) -> list[dict]:
    from src.tools.db_tools import _connect
    with _connect("admin") as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT step, detail, created_at FROM audit_log "
            "WHERE request_id = %s ORDER BY created_at ASC",
            (request_id,),
        )
        return [
            {"step": row[0], "detail": row[1], "created_at": row[2].isoformat()}
            for row in cur.fetchall()
        ]
