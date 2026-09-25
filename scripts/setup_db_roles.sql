-- Prior Authorization Agent — guardrail enforcement at the database level.
-- Matches LLD Section 2. Same DB-role-enforcement pattern used in the
-- cold-chain project: guardrails live in the database, not just the prompt.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'pa_agent_role') THEN
        CREATE ROLE pa_agent_role LOGIN PASSWORD 'change_me_in_env';
    END IF;
END
$$;

-- Read-only on reference/request data — the agent must never alter what a
-- request actually said.
GRANT SELECT ON members, plans, providers, pa_requests, pa_request_documents
    TO pa_agent_role;

-- Can record its own decisions and audit entries, but never edit or remove one
-- once written — this is what makes the audit trail trustworthy as compliance
-- evidence rather than just a debug log.
GRANT SELECT, INSERT ON pa_decisions TO pa_agent_role;
GRANT INSERT ON audit_log TO pa_agent_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pa_agent_role;

REVOKE UPDATE, DELETE ON pa_decisions FROM pa_agent_role;
REVOKE UPDATE, DELETE ON audit_log FROM pa_agent_role;
REVOKE INSERT, UPDATE, DELETE ON members, plans, providers, pa_requests,
    pa_request_documents FROM pa_agent_role;

-- Separate compliance/admin role: read-only across everything including the
-- audit log, but cannot write anywhere at all. This is the credential the
-- audit-log viewer UI (LLD Section 3.6 / HLD 3.5) connects with — never the
-- agent's own role, so "who can see the trail" and "who can write to it"
-- stay cleanly separated.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'pa_admin_role') THEN
        CREATE ROLE pa_admin_role LOGIN PASSWORD 'change_me_in_env';
    END IF;
END
$$;

GRANT SELECT ON members, plans, providers, pa_requests, pa_request_documents,
    pa_decisions, audit_log TO pa_admin_role;
