BEGIN;

ALTER TABLE knowledge.documents
    ADD COLUMN IF NOT EXISTS revision integer NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS is_current boolean NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS supersedes_id uuid REFERENCES knowledge.documents(id);

DROP INDEX IF EXISTS knowledge.knowledge_documents_canonical_url_uniq;
DROP INDEX IF EXISTS knowledge.knowledge_documents_source_hash_uniq;
CREATE UNIQUE INDEX IF NOT EXISTS knowledge_documents_current_canonical_url_uniq
    ON knowledge.documents (canonical_url)
    WHERE canonical_url IS NOT NULL AND is_current;
CREATE UNIQUE INDEX IF NOT EXISTS knowledge_documents_current_source_hash_uniq
    ON knowledge.documents (source_id, content_hash)
    WHERE is_current;
CREATE INDEX IF NOT EXISTS knowledge_documents_revision_idx
    ON knowledge.documents (canonical_url, revision DESC)
    WHERE canonical_url IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS knowledge_documents_canonical_revision_uniq
    ON knowledge.documents (canonical_url, revision)
    WHERE canonical_url IS NOT NULL;

CREATE TABLE IF NOT EXISTS knowledge.ingestion_sources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    seed_url text NOT NULL,
    source_name text NOT NULL,
    domain text NOT NULL,
    discovery_mode text NOT NULL CHECK (discovery_mode IN ('auto', 'sitemap', 'feed')),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    spec jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (seed_url, source_name)
);

CREATE TABLE IF NOT EXISTS knowledge.sync_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ingestion_source_id uuid NOT NULL REFERENCES knowledge.ingestion_sources(id),
    status text NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'running', 'completed', 'failed', 'cancelling', 'cancelled')
    ),
    cancel_requested boolean NOT NULL DEFAULT false,
    spec jsonb NOT NULL DEFAULT '{}'::jsonb,
    discovered_count integer NOT NULL DEFAULT 0,
    queued_count integer NOT NULL DEFAULT 0,
    processing_count integer NOT NULL DEFAULT 0,
    completed_count integer NOT NULL DEFAULT 0,
    failed_count integer NOT NULL DEFAULT 0,
    skipped_count integer NOT NULL DEFAULT 0,
    cancelled_count integer NOT NULL DEFAULT 0,
    warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS knowledge.crawl_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES knowledge.sync_runs(id) ON DELETE CASCADE,
    url text NOT NULL,
    published_at timestamptz,
    modified_at timestamptz,
    source_kind text NOT NULL,
    status text NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'processing', 'retry', 'completed', 'failed', 'skipped', 'cancelled')
    ),
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    lease_token uuid,
    lease_expires_at timestamptz,
    document_id uuid REFERENCES knowledge.documents(id),
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, url)
);

CREATE INDEX IF NOT EXISTS knowledge_crawl_items_lease_idx
    ON knowledge.crawl_items (status, next_attempt_at, lease_expires_at);
CREATE INDEX IF NOT EXISTS knowledge_sync_runs_status_idx
    ON knowledge.sync_runs (status, created_at);

DROP TRIGGER IF EXISTS knowledge_ingestion_sources_updated_at ON knowledge.ingestion_sources;
CREATE TRIGGER knowledge_ingestion_sources_updated_at
    BEFORE UPDATE ON knowledge.ingestion_sources
    FOR EACH ROW EXECUTE FUNCTION knowledge.set_updated_at();
DROP TRIGGER IF EXISTS knowledge_sync_runs_updated_at ON knowledge.sync_runs;
CREATE TRIGGER knowledge_sync_runs_updated_at
    BEFORE UPDATE ON knowledge.sync_runs
    FOR EACH ROW EXECUTE FUNCTION knowledge.set_updated_at();
DROP TRIGGER IF EXISTS knowledge_crawl_items_updated_at ON knowledge.crawl_items;
CREATE TRIGGER knowledge_crawl_items_updated_at
    BEFORE UPDATE ON knowledge.crawl_items
    FOR EACH ROW EXECUTE FUNCTION knowledge.set_updated_at();

INSERT INTO knowledge.schema_migrations(version)
VALUES ('002_batch_ingestion')
ON CONFLICT (version) DO NOTHING;

COMMIT;
