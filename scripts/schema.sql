-- Prior Authorization Agent — schema
-- Matches LLD Section 2. Synthetic data only — no real PHI.

CREATE TABLE IF NOT EXISTS plans (
    plan_id         UUID PRIMARY KEY,
    plan_name       VARCHAR(200) NOT NULL,
    plan_year       INT NOT NULL
);

CREATE TABLE IF NOT EXISTS providers (
    provider_id     UUID PRIMARY KEY,
    npi             VARCHAR(20) NOT NULL,
    provider_name   VARCHAR(200) NOT NULL
);

CREATE TABLE IF NOT EXISTS members (
    member_id       UUID PRIMARY KEY,
    plan_id         UUID NOT NULL REFERENCES plans(plan_id),
    date_of_birth   DATE NOT NULL,
    gender          VARCHAR(20)
);

CREATE TABLE IF NOT EXISTS pa_requests (
    request_id           UUID PRIMARY KEY,
    member_id            UUID NOT NULL REFERENCES members(member_id),
    provider_id          UUID NOT NULL REFERENCES providers(provider_id),
    service_code         VARCHAR(20) NOT NULL,
    service_description  TEXT,
    submitted_at          TIMESTAMPTZ NOT NULL,
    status                VARCHAR(20) NOT NULL,
    request_type          VARCHAR(20) NOT NULL
);

CREATE TABLE IF NOT EXISTS pa_request_documents (
    document_id     UUID PRIMARY KEY,
    request_id      UUID NOT NULL REFERENCES pa_requests(request_id),
    doc_type        VARCHAR(50) NOT NULL,
    content_ref     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pa_decisions (
    decision_id     UUID PRIMARY KEY,
    request_id      UUID NOT NULL REFERENCES pa_requests(request_id),
    outcome         VARCHAR(20) NOT NULL,
    citation        TEXT,
    confidence      FLOAT NOT NULL,
    decided_at      TIMESTAMPTZ NOT NULL,
    decided_by      VARCHAR(20) NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    log_id          BIGSERIAL PRIMARY KEY,
    request_id      UUID NOT NULL,
    step            VARCHAR(50) NOT NULL,
    detail          JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
