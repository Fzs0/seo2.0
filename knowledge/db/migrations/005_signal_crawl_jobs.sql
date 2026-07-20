BEGIN;

CREATE TABLE IF NOT EXISTS knowledge.market_signal_crawl_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL UNIQUE,
    source_name text NOT NULL,
    platform text NOT NULL,
    channel text NOT NULL DEFAULT 'social',
    rights_confirmed boolean NOT NULL DEFAULT false,
    search_url_template text NOT NULL,
    keywords jsonb NOT NULL,
    detail_url_contains jsonb NOT NULL,
    max_pages integer NOT NULL DEFAULT 3 CHECK (max_pages BETWEEN 1 AND 20),
    max_signals integer NOT NULL DEFAULT 100 CHECK (max_signals BETWEEN 1 AND 500),
    delay_ms integer NOT NULL DEFAULT 1000 CHECK (delay_ms BETWEEN 250 AND 60000),
    interval_hours integer NOT NULL DEFAULT 24 CHECK (interval_hours BETWEEN 1 AND 168),
    enabled boolean NOT NULL DEFAULT true,
    next_run_at timestamptz,
    last_run_at timestamptz,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (length(btrim(name)) > 0),
    CHECK (length(btrim(source_name)) > 0),
    CHECK (length(btrim(platform)) > 0),
    CHECK (length(btrim(search_url_template)) > 0)
);

CREATE TABLE IF NOT EXISTS knowledge.market_signal_crawl_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES knowledge.market_signal_crawl_jobs(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    counts jsonb NOT NULL DEFAULT '{}'::jsonb,
    warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz
);

CREATE INDEX IF NOT EXISTS knowledge_market_signal_crawl_jobs_due_idx
    ON knowledge.market_signal_crawl_jobs (enabled, next_run_at);
CREATE INDEX IF NOT EXISTS knowledge_market_signal_crawl_runs_job_idx
    ON knowledge.market_signal_crawl_runs (job_id, created_at DESC);

DROP TRIGGER IF EXISTS knowledge_market_signal_crawl_jobs_updated_at
    ON knowledge.market_signal_crawl_jobs;
CREATE TRIGGER knowledge_market_signal_crawl_jobs_updated_at
    BEFORE UPDATE ON knowledge.market_signal_crawl_jobs
    FOR EACH ROW EXECUTE FUNCTION knowledge.set_updated_at();

INSERT INTO knowledge.schema_migrations(version)
VALUES ('005_signal_crawl_jobs')
ON CONFLICT (version) DO NOTHING;

COMMIT;
