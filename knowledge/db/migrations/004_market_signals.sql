BEGIN;

CREATE TABLE IF NOT EXISTS knowledge.market_signals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id uuid NOT NULL REFERENCES knowledge.sources(id) ON DELETE CASCADE,
    platform text NOT NULL,
    external_id text,
    canonical_url text,
    content_kind text NOT NULL DEFAULT 'post',
    title text,
    content text NOT NULL,
    author_handle text,
    thread_key text,
    parent_external_id text,
    language_code text NOT NULL DEFAULT 'en',
    market text,
    published_at timestamptz,
    engagement jsonb NOT NULL DEFAULT '{}'::jsonb,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    content_hash char(64) NOT NULL,
    status text NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'analyzed', 'ignored')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (length(btrim(content_kind)) > 0),
    CHECK (length(btrim(platform)) > 0),
    CHECK (length(btrim(content)) > 0),
    CHECK (length(btrim(language_code)) > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS knowledge_market_signals_source_external_uniq
    ON knowledge.market_signals (source_id, external_id)
    WHERE external_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS knowledge_market_signals_source_hash_uniq
    ON knowledge.market_signals (source_id, content_hash);
CREATE INDEX IF NOT EXISTS knowledge_market_signals_source_published_idx
    ON knowledge.market_signals (source_id, COALESCE(published_at, created_at) DESC);
CREATE INDEX IF NOT EXISTS knowledge_market_signals_kind_published_idx
    ON knowledge.market_signals (content_kind, published_at DESC);
CREATE INDEX IF NOT EXISTS knowledge_market_signals_thread_idx
    ON knowledge.market_signals (source_id, thread_key, published_at);

DROP TRIGGER IF EXISTS knowledge_market_signals_updated_at ON knowledge.market_signals;
CREATE TRIGGER knowledge_market_signals_updated_at
    BEFORE UPDATE ON knowledge.market_signals
    FOR EACH ROW EXECUTE FUNCTION knowledge.set_updated_at();

INSERT INTO knowledge.schema_migrations(version)
VALUES ('004_market_signals')
ON CONFLICT (version) DO NOTHING;

COMMIT;
