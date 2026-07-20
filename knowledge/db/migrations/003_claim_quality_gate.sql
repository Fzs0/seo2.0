BEGIN;

ALTER TABLE knowledge.claims
    ADD COLUMN IF NOT EXISTS quality_status text NOT NULL DEFAULT 'unreviewed',
    ADD COLUMN IF NOT EXISTS quality_utility_score numeric(4,3),
    ADD COLUMN IF NOT EXISTS quality_reviewer_confidence numeric(4,3),
    ADD COLUMN IF NOT EXISTS quality_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS quality_note text,
    ADD COLUMN IF NOT EXISTS quality_model text,
    ADD COLUMN IF NOT EXISTS quality_prompt_version text,
    ADD COLUMN IF NOT EXISTS quality_reviewed_at timestamptz;

ALTER TABLE knowledge.claims DROP CONSTRAINT IF EXISTS knowledge_claims_quality_status_check;
ALTER TABLE knowledge.claims ADD CONSTRAINT knowledge_claims_quality_status_check
    CHECK (quality_status IN ('unreviewed', 'keep', 'reject', 'uncertain', 'error'));
ALTER TABLE knowledge.claims DROP CONSTRAINT IF EXISTS knowledge_claims_quality_utility_check;
ALTER TABLE knowledge.claims ADD CONSTRAINT knowledge_claims_quality_utility_check
    CHECK (quality_utility_score IS NULL OR quality_utility_score BETWEEN 0 AND 1);
ALTER TABLE knowledge.claims DROP CONSTRAINT IF EXISTS knowledge_claims_quality_confidence_check;
ALTER TABLE knowledge.claims ADD CONSTRAINT knowledge_claims_quality_confidence_check
    CHECK (quality_reviewer_confidence IS NULL OR quality_reviewer_confidence BETWEEN 0 AND 1);

CREATE TABLE IF NOT EXISTS knowledge.claim_quality_reviews (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id uuid NOT NULL REFERENCES knowledge.claims(id),
    run_id uuid,
    action text NOT NULL CHECK (action IN ('classification', 'apply', 'restore')),
    machine_decision text NOT NULL CHECK (machine_decision IN ('keep', 'reject', 'uncertain', 'error')),
    effective_decision text NOT NULL CHECK (effective_decision IN ('keep', 'reject', 'uncertain', 'error')),
    utility_score numeric(4,3),
    reviewer_confidence numeric(4,3),
    reason_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
    rationale text,
    model text,
    prompt_version text,
    dry_run boolean NOT NULL,
    applied boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS knowledge.quality_review_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    status text NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'running', 'completed', 'failed', 'cancelling', 'cancelled', 'applied')
    ),
    limit_documents integer NOT NULL CHECK (limit_documents BETWEEN 1 AND 1000),
    include_reviewed boolean NOT NULL DEFAULT false,
    cancel_requested boolean NOT NULL DEFAULT false,
    queued_count integer NOT NULL DEFAULT 0,
    processing_count integer NOT NULL DEFAULT 0,
    completed_count integer NOT NULL DEFAULT 0,
    failed_count integer NOT NULL DEFAULT 0,
    cancelled_count integer NOT NULL DEFAULT 0,
    auto_reject_count integer NOT NULL DEFAULT 0,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    applied_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE knowledge.claim_quality_reviews
    DROP CONSTRAINT IF EXISTS claim_quality_reviews_run_id_fkey;
ALTER TABLE knowledge.claim_quality_reviews
    ADD CONSTRAINT claim_quality_reviews_run_id_fkey
    FOREIGN KEY (run_id) REFERENCES knowledge.quality_review_runs(id);

CREATE TABLE IF NOT EXISTS knowledge.quality_review_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES knowledge.quality_review_runs(id) ON DELETE CASCADE,
    document_id uuid NOT NULL REFERENCES knowledge.documents(id),
    status text NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'processing', 'retry', 'completed', 'failed', 'cancelled', 'applied')
    ),
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    lease_token uuid,
    lease_expires_at timestamptz,
    reviewed_claims integer NOT NULL DEFAULT 0,
    auto_reject_count integer NOT NULL DEFAULT 0,
    document_decision text,
    document_reviewer_confidence numeric(4,3),
    document_reason_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
    document_rationale text,
    results jsonb NOT NULL DEFAULT '[]'::jsonb,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, document_id)
);

ALTER TABLE knowledge.quality_review_items
    DROP CONSTRAINT IF EXISTS quality_review_items_document_decision_check;
ALTER TABLE knowledge.quality_review_items
    ADD CONSTRAINT quality_review_items_document_decision_check
    CHECK (document_decision IS NULL OR document_decision IN ('keep', 'reject', 'uncertain'));
ALTER TABLE knowledge.quality_review_items
    DROP CONSTRAINT IF EXISTS quality_review_items_document_confidence_check;
ALTER TABLE knowledge.quality_review_items
    ADD CONSTRAINT quality_review_items_document_confidence_check
    CHECK (document_reviewer_confidence IS NULL OR document_reviewer_confidence BETWEEN 0 AND 1);

CREATE INDEX IF NOT EXISTS knowledge_claims_quality_status_idx
    ON knowledge.claims (quality_status, review_status, document_id);
CREATE INDEX IF NOT EXISTS knowledge_quality_review_items_lease_idx
    ON knowledge.quality_review_items (status, next_attempt_at, lease_expires_at);
CREATE INDEX IF NOT EXISTS knowledge_claim_quality_reviews_claim_idx
    ON knowledge.claim_quality_reviews (claim_id, created_at DESC);

CREATE OR REPLACE FUNCTION knowledge.reject_claim_quality_review_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'claim_quality_reviews is append-only';
END;
$$;

DROP TRIGGER IF EXISTS knowledge_claim_quality_reviews_append_only
    ON knowledge.claim_quality_reviews;
CREATE TRIGGER knowledge_claim_quality_reviews_append_only
    BEFORE UPDATE OR DELETE ON knowledge.claim_quality_reviews
    FOR EACH ROW EXECUTE FUNCTION knowledge.reject_claim_quality_review_mutation();

DROP TRIGGER IF EXISTS knowledge_quality_review_runs_updated_at
    ON knowledge.quality_review_runs;
CREATE TRIGGER knowledge_quality_review_runs_updated_at
    BEFORE UPDATE ON knowledge.quality_review_runs
    FOR EACH ROW EXECUTE FUNCTION knowledge.set_updated_at();
DROP TRIGGER IF EXISTS knowledge_quality_review_items_updated_at
    ON knowledge.quality_review_items;
CREATE TRIGGER knowledge_quality_review_items_updated_at
    BEFORE UPDATE ON knowledge.quality_review_items
    FOR EACH ROW EXECUTE FUNCTION knowledge.set_updated_at();

INSERT INTO knowledge.schema_migrations(version)
VALUES ('003_claim_quality_gate')
ON CONFLICT (version) DO NOTHING;

COMMIT;
