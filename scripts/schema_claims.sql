-- Claims / Payment Integrity — schema additions (Phase 2)
-- Matches Claims LLD Section 2. Synthetic data only — no real PHI.
-- Depends on schema.sql already being applied (members, plans, providers,
-- pa_decisions all referenced here).

CREATE TABLE IF NOT EXISTS claims (
    claim_id            UUID PRIMARY KEY,
    member_id           UUID NOT NULL REFERENCES members(member_id),
    provider_id         UUID NOT NULL REFERENCES providers(provider_id),
    service_code        VARCHAR(20) NOT NULL,
    service_description TEXT,
    billed_amount       NUMERIC(10,2) NOT NULL,
    date_of_service     DATE NOT NULL,
    submitted_at        TIMESTAMPTZ NOT NULL,
    status              VARCHAR(20) NOT NULL,   -- submitted / pending_docs / decided
    clinical_notes      TEXT
);

CREATE TABLE IF NOT EXISTS claim_documents (
    document_id     UUID PRIMARY KEY,
    claim_id        UUID NOT NULL REFERENCES claims(claim_id),
    doc_type        VARCHAR(50) NOT NULL,
    content_ref     TEXT NOT NULL
);

-- Records each specialist's signal individually, not just the reconciled
-- outcome (confirmed design decision) — an SIU investigator or auditor
-- needs to see which agent flagged what, at what confidence.
CREATE TABLE IF NOT EXISTS claim_decisions (
    decision_id       UUID PRIMARY KEY,
    claim_id          UUID NOT NULL REFERENCES claims(claim_id),
    outcome           VARCHAR(20) NOT NULL,    -- pay / deny / flag_siu / escalate
    citation          TEXT,                     -- required if outcome = deny
    coverage_signal   JSONB NOT NULL,
    pa_xref_signal    JSONB NOT NULL,
    fraud_signal      JSONB NOT NULL,
    confidence        FLOAT NOT NULL,
    decided_at        TIMESTAMPTZ NOT NULL,
    decided_by        VARCHAR(20) NOT NULL     -- 'agent' or reviewer_id
);
