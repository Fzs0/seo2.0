BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS knowledge;

CREATE TABLE IF NOT EXISTS knowledge.schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION knowledge.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TABLE IF NOT EXISTS knowledge.sources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    channel text NOT NULL DEFAULT 'seo',
    source_type text NOT NULL DEFAULT 'manual',
    base_url text,
    domain text,
    rights_confirmed boolean NOT NULL DEFAULT false,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (length(btrim(name)) > 0),
    CHECK (length(btrim(channel)) > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS knowledge_sources_name_channel_uniq
    ON knowledge.sources (lower(name), channel);
CREATE INDEX IF NOT EXISTS knowledge_sources_channel_status_idx
    ON knowledge.sources (channel, status);

CREATE TABLE IF NOT EXISTS knowledge.documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id uuid NOT NULL REFERENCES knowledge.sources(id) ON DELETE CASCADE,
    canonical_url text,
    title text NOT NULL,
    content_type text NOT NULL DEFAULT 'article',
    language_code text NOT NULL DEFAULT 'en',
    market text,
    author text,
    published_at timestamptz,
    raw_content text NOT NULL,
    content_hash char(64) NOT NULL,
    status text NOT NULL DEFAULT 'imported' CHECK (status IN ('imported', 'processed', 'failed')),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(raw_content, ''))
    ) STORED,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (length(btrim(title)) > 0),
    CHECK (length(btrim(raw_content)) > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS knowledge_documents_source_hash_uniq
    ON knowledge.documents (source_id, content_hash);
CREATE UNIQUE INDEX IF NOT EXISTS knowledge_documents_canonical_url_uniq
    ON knowledge.documents (canonical_url)
    WHERE canonical_url IS NOT NULL;
CREATE INDEX IF NOT EXISTS knowledge_documents_source_created_idx
    ON knowledge.documents (source_id, created_at DESC);
CREATE INDEX IF NOT EXISTS knowledge_documents_search_idx
    ON knowledge.documents USING gin (search_vector);

CREATE TABLE IF NOT EXISTS knowledge.claims (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES knowledge.documents(id) ON DELETE CASCADE,
    channel text NOT NULL,
    topic text,
    knowledge_type text NOT NULL DEFAULT 'article_insight',
    statement text NOT NULL,
    conditions jsonb NOT NULL DEFAULT '[]'::jsonb,
    exceptions jsonb NOT NULL DEFAULT '[]'::jsonb,
    recommended_action text,
    confidence numeric(4,3) NOT NULL DEFAULT 0.550 CHECK (confidence >= 0 AND confidence <= 1),
    review_status text NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending', 'approved', 'rejected')),
    review_note text,
    reviewed_by text,
    reviewed_at timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector(
            'simple',
            coalesce(topic, '') || ' ' || coalesce(statement, '') || ' ' || coalesce(recommended_action, '')
        )
    ) STORED,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (length(btrim(channel)) > 0),
    CHECK (length(btrim(statement)) > 0)
);

CREATE INDEX IF NOT EXISTS knowledge_claims_review_channel_idx
    ON knowledge.claims (review_status, channel, created_at DESC);
CREATE INDEX IF NOT EXISTS knowledge_claims_document_idx
    ON knowledge.claims (document_id);
CREATE INDEX IF NOT EXISTS knowledge_claims_search_idx
    ON knowledge.claims USING gin (search_vector);

CREATE TABLE IF NOT EXISTS knowledge.evidence (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id uuid NOT NULL REFERENCES knowledge.claims(id) ON DELETE CASCADE,
    document_id uuid NOT NULL REFERENCES knowledge.documents(id) ON DELETE CASCADE,
    excerpt text NOT NULL,
    locator text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (length(btrim(excerpt)) > 0)
);

CREATE INDEX IF NOT EXISTS knowledge_evidence_claim_idx
    ON knowledge.evidence (claim_id);
CREATE UNIQUE INDEX IF NOT EXISTS knowledge_evidence_claim_locator_uniq
    ON knowledge.evidence (claim_id, coalesce(locator, ''));

CREATE TABLE IF NOT EXISTS knowledge.usages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id uuid REFERENCES knowledge.claims(id) ON DELETE SET NULL,
    stage text NOT NULL,
    query text,
    context jsonb NOT NULL DEFAULT '{}'::jsonb,
    result jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (length(btrim(stage)) > 0)
);

CREATE INDEX IF NOT EXISTS knowledge_usages_claim_created_idx
    ON knowledge.usages (claim_id, created_at DESC);

DROP TRIGGER IF EXISTS knowledge_sources_updated_at ON knowledge.sources;
CREATE TRIGGER knowledge_sources_updated_at
    BEFORE UPDATE ON knowledge.sources
    FOR EACH ROW EXECUTE FUNCTION knowledge.set_updated_at();

DROP TRIGGER IF EXISTS knowledge_documents_updated_at ON knowledge.documents;
CREATE TRIGGER knowledge_documents_updated_at
    BEFORE UPDATE ON knowledge.documents
    FOR EACH ROW EXECUTE FUNCTION knowledge.set_updated_at();

DROP TRIGGER IF EXISTS knowledge_claims_updated_at ON knowledge.claims;
CREATE TRIGGER knowledge_claims_updated_at
    BEFORE UPDATE ON knowledge.claims
    FOR EACH ROW EXECUTE FUNCTION knowledge.set_updated_at();

INSERT INTO knowledge.schema_migrations(version)
VALUES ('001_init')
ON CONFLICT (version) DO NOTHING;

COMMIT;
