-- Claims / Payment Integrity — guardrail roles (Phase 2)
-- Matches Claims LLD Section 2. Depends on setup_db_roles.sql already
-- having been applied (pa_admin_role must already exist — this file
-- extends it, not recreates it).

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'claims_agent_role') THEN
        CREATE ROLE claims_agent_role LOGIN PASSWORD 'change_me_in_env';
    END IF;
END
$$;

-- Read-only on claims data AND on Phase 1's pa_decisions — this is the
-- literal DB-level expression of "Phase 2 reads Phase 1's output" from the
-- combined architecture diagram.
GRANT SELECT ON claims, claim_documents, members, plans, providers, pa_decisions
    TO claims_agent_role;

-- Can record its own decisions and audit entries, same append-only pattern
-- as pa_agent_role. Cannot write to claims/claim_documents at all — the
-- role that submits a claim is not the role that decides it.
GRANT SELECT, INSERT ON claim_decisions TO claims_agent_role;
GRANT INSERT ON audit_log TO claims_agent_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO claims_agent_role;
GRANT CONNECT ON DATABASE pa_agent_poc TO claims_agent_role;

REVOKE UPDATE, DELETE ON claim_decisions FROM claims_agent_role;
REVOKE INSERT, UPDATE, DELETE ON claims, claim_documents, pa_decisions
    FROM claims_agent_role;

-- Claims intake role — submission only, mirrors pa_intake_role exactly.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'claims_intake_role') THEN
        CREATE ROLE claims_intake_role LOGIN PASSWORD 'change_me_in_env';
    END IF;
END
$$;

GRANT SELECT, INSERT ON claims, claim_documents TO claims_intake_role;
GRANT SELECT ON members, plans, providers, claim_decisions, pa_decisions
    TO claims_intake_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO claims_intake_role;
GRANT CONNECT ON DATABASE pa_agent_poc TO claims_intake_role;
REVOKE UPDATE, DELETE ON claims, claim_documents FROM claims_intake_role;

-- pa_admin_role is EXTENDED, not duplicated — it's already the audit/reviewer
-- role for Phase 1; Phase 2's SIU and claims-examiner queues are the same
-- kind of human-reviewer function, just a second queue on the same role.
GRANT SELECT ON claims, claim_documents, claim_decisions TO pa_admin_role;
GRANT INSERT ON claim_decisions TO pa_admin_role;
