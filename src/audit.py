"""Audit logging. Every node writes one row before returning — inline, not a
separate pass at the end — so a crash mid-graph still leaves a trail (LLD
Section 4). The agent role can only INSERT here; see setup_db_roles.sql."""

import json
from src.tools.db_tools import _connect


def write_audit_entry(request_id: str, step: str, detail: dict) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO audit_log (request_id, step, detail) VALUES (%s, %s, %s)",
            (request_id, step, json.dumps(detail)),
        )
        conn.commit()


def get_audit_trail(request_id: str) -> list[dict]:
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
