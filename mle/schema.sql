CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS companies (
    registration_number text PRIMARY KEY,
    company_name text,
    industry text,
    state text,
    nature_of_business text,
    event_at timestamptz NOT NULL,
    active boolean NOT NULL,
    body_hash text NOT NULL,
    embedding vector(768),
    model_revision text,
    CHECK ((embedding IS NULL) = (model_revision IS NULL)),
    CHECK (active OR embedding IS NULL),
    CHECK (embedding IS NULL OR nullif(btrim(nature_of_business), '') IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS rejected_events (
    body_hash text PRIMARY KEY,
    reason text NOT NULL
);