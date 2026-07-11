-- SEO Workbench Agent Memory Schema v1
-- PostgreSQL 14+
-- Run this in the target database once. It creates an isolated seo_agent schema.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS seo_agent;

CREATE OR REPLACE FUNCTION seo_agent.set_updated_at()
RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS seo_agent.sites (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  site_key text NOT NULL UNIQUE,
  name text NOT NULL,
  site_type text NOT NULL CHECK (site_type IN ('main', 'wp', 'blog', 'other')),
  domain text,
  base_url text,
  api_base_url text,
  market text,
  language_code text,
  google_gl text,
  google_hl text,
  semrush_database text,
  content_role text,
  content_scope text,
  is_main boolean NOT NULL DEFAULT false,
  allow_external_links boolean NOT NULL DEFAULT false,
  publish_config jsonb NOT NULL DEFAULT '{}'::jsonb,
  api_config jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'archived')),
  notes text,
  raw jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS seo_agent.keywords (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  keyword text NOT NULL,
  normalized_keyword text GENERATED ALWAYS AS (lower(regexp_replace(trim(keyword), '\s+', ' ', 'g'))) STORED,
  source text NOT NULL DEFAULT 'semrush',
  source_file text,
  semrush_database text,
  market text,
  language_code text,
  google_gl text,
  google_hl text,
  volume integer NOT NULL DEFAULT 0,
  kd numeric(6,2) NOT NULL DEFAULT 0,
  cpc numeric(10,2) NOT NULL DEFAULT 0,
  intent text,
  serp_features jsonb NOT NULL DEFAULT '[]'::jsonb,
  topic_cluster text,
  seed_keyword text,
  page_group text,
  assigned_site_id uuid REFERENCES seo_agent.sites(id) ON DELETE SET NULL,
  assigned_site_label text,
  page_type text,
  page_role text,
  target_asset_url text,
  asset_status text,
  content_action text,
  priority text CHECK (priority IN ('P0', 'P1', 'P2', 'P3', 'Hold')),
  score numeric(6,2),
  status text NOT NULL DEFAULT 'imported' CHECK (
    status IN ('imported', 'analyzed', 'planned', 'queued', 'written', 'published', 'reviewed', 'hold', 'dropped')
  ),
  reason text,
  ai_review jsonb NOT NULL DEFAULT '{}'::jsonb,
  raw jsonb NOT NULL DEFAULT '{}'::jsonb,
  imported_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS seo_agent.posts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id uuid NOT NULL REFERENCES seo_agent.sites(id) ON DELETE CASCADE,
  external_id text,
  title text NOT NULL,
  slug text,
  url text,
  status text,
  author text,
  category_id text,
  language_code text,
  market text,
  primary_keyword_id uuid REFERENCES seo_agent.keywords(id) ON DELETE SET NULL,
  primary_keyword text,
  topic_cluster text,
  page_type text,
  content_format text NOT NULL DEFAULT 'markdown' CHECK (content_format IN ('markdown', 'html', 'mixed', 'unknown')),
  content_md text,
  content_html text,
  excerpt text,
  meta_title text,
  meta_description text,
  meta_keywords jsonb NOT NULL DEFAULT '[]'::jsonb,
  cover_url text,
  published_at timestamptz,
  modified_at timestamptz,
  fetched_at timestamptz NOT NULL DEFAULT now(),
  source text NOT NULL DEFAULT 'api',
  raw jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS seo_agent.serp_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  keyword_id uuid REFERENCES seo_agent.keywords(id) ON DELETE SET NULL,
  keyword text NOT NULL,
  normalized_keyword text GENERATED ALWAYS AS (lower(regexp_replace(trim(keyword), '\s+', ' ', 'g'))) STORED,
  engine text NOT NULL DEFAULT 'google',
  google_gl text,
  google_hl text,
  location text,
  requested_at timestamptz NOT NULL DEFAULT now(),
  top_result_count integer NOT NULL DEFAULT 0,
  organic_results jsonb NOT NULL DEFAULT '[]'::jsonb,
  related_questions jsonb NOT NULL DEFAULT '[]'::jsonb,
  related_searches jsonb NOT NULL DEFAULT '[]'::jsonb,
  page_type_summary text,
  intent_summary text,
  competitor_gaps jsonb NOT NULL DEFAULT '[]'::jsonb,
  raw jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS seo_agent.tasks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  task_type text NOT NULL CHECK (
    task_type IN ('keyword_review', 'serp_check', 'new_article', 'update_article', 'internal_link', 'publish', 'review', 'product_extract')
  ),
  status text NOT NULL DEFAULT 'queued' CHECK (
    status IN ('queued', 'running', 'blocked', 'failed', 'done', 'canceled', 'skipped')
  ),
  priority text CHECK (priority IN ('P0', 'P1', 'P2', 'P3', 'Hold')),
  score numeric(6,2),
  site_id uuid REFERENCES seo_agent.sites(id) ON DELETE SET NULL,
  keyword_id uuid REFERENCES seo_agent.keywords(id) ON DELETE SET NULL,
  post_id uuid REFERENCES seo_agent.posts(id) ON DELETE SET NULL,
  article_id uuid,
  serp_snapshot_id uuid REFERENCES seo_agent.serp_snapshots(id) ON DELETE SET NULL,
  target_url text,
  title text,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  required_data text[] NOT NULL DEFAULT ARRAY[]::text[],
  decision jsonb NOT NULL DEFAULT '{}'::jsonb,
  logs jsonb NOT NULL DEFAULT '[]'::jsonb,
  error_message text,
  run_after timestamptz,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS seo_agent.articles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id uuid REFERENCES seo_agent.tasks(id) ON DELETE SET NULL,
  site_id uuid REFERENCES seo_agent.sites(id) ON DELETE SET NULL,
  keyword_id uuid REFERENCES seo_agent.keywords(id) ON DELETE SET NULL,
  serp_snapshot_id uuid REFERENCES seo_agent.serp_snapshots(id) ON DELETE SET NULL,
  title text NOT NULL,
  slug text,
  target_url text,
  status text NOT NULL DEFAULT 'generated' CHECK (
    status IN ('draft', 'generated', 'approved', 'published', 'failed', 'archived')
  ),
  language_code text,
  market text,
  brief_md text,
  prompt_text text,
  content_md text,
  content_html text,
  article_parts jsonb NOT NULL DEFAULT '{}'::jsonb,
  meta_title text,
  meta_description text,
  primary_keyword text,
  secondary_keywords text[] NOT NULL DEFAULT ARRAY[]::text[],
  internal_link_plan jsonb NOT NULL DEFAULT '[]'::jsonb,
  image_plan jsonb NOT NULL DEFAULT '[]'::jsonb,
  references_plan jsonb NOT NULL DEFAULT '[]'::jsonb,
  qa_checklist jsonb NOT NULL DEFAULT '[]'::jsonb,
  generation_provider text,
  generation_model text,
  published_post_id text,
  published_url text,
  published_at timestamptz,
  saved_path text,
  log_path text,
  raw_ai_response jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE seo_agent.tasks
  DROP CONSTRAINT IF EXISTS tasks_article_id_fkey;

ALTER TABLE seo_agent.tasks
  ADD CONSTRAINT tasks_article_id_fkey
  FOREIGN KEY (article_id) REFERENCES seo_agent.articles(id) ON DELETE SET NULL;

DROP TRIGGER IF EXISTS set_sites_updated_at ON seo_agent.sites;
CREATE TRIGGER set_sites_updated_at
BEFORE UPDATE ON seo_agent.sites
FOR EACH ROW EXECUTE FUNCTION seo_agent.set_updated_at();

DROP TRIGGER IF EXISTS set_keywords_updated_at ON seo_agent.keywords;
CREATE TRIGGER set_keywords_updated_at
BEFORE UPDATE ON seo_agent.keywords
FOR EACH ROW EXECUTE FUNCTION seo_agent.set_updated_at();

DROP TRIGGER IF EXISTS set_posts_updated_at ON seo_agent.posts;
CREATE TRIGGER set_posts_updated_at
BEFORE UPDATE ON seo_agent.posts
FOR EACH ROW EXECUTE FUNCTION seo_agent.set_updated_at();

DROP TRIGGER IF EXISTS set_tasks_updated_at ON seo_agent.tasks;
CREATE TRIGGER set_tasks_updated_at
BEFORE UPDATE ON seo_agent.tasks
FOR EACH ROW EXECUTE FUNCTION seo_agent.set_updated_at();

DROP TRIGGER IF EXISTS set_articles_updated_at ON seo_agent.articles;
CREATE TRIGGER set_articles_updated_at
BEFORE UPDATE ON seo_agent.articles
FOR EACH ROW EXECUTE FUNCTION seo_agent.set_updated_at();

CREATE UNIQUE INDEX IF NOT EXISTS keywords_unique_import_scope
ON seo_agent.keywords (normalized_keyword, coalesce(semrush_database, ''), coalesce(market, ''), coalesce(language_code, ''));

CREATE UNIQUE INDEX IF NOT EXISTS posts_unique_external_id
ON seo_agent.posts (site_id, external_id)
WHERE external_id IS NOT NULL AND external_id <> '';

CREATE UNIQUE INDEX IF NOT EXISTS posts_unique_url
ON seo_agent.posts (site_id, url)
WHERE url IS NOT NULL AND url <> '';

CREATE INDEX IF NOT EXISTS sites_market_language_idx
ON seo_agent.sites (market, language_code, site_type, status);

CREATE INDEX IF NOT EXISTS keywords_status_priority_idx
ON seo_agent.keywords (status, priority, score DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS keywords_assigned_site_idx
ON seo_agent.keywords (assigned_site_id, status, priority);

CREATE INDEX IF NOT EXISTS keywords_text_idx
ON seo_agent.keywords (normalized_keyword);

CREATE INDEX IF NOT EXISTS posts_site_status_idx
ON seo_agent.posts (site_id, status, published_at DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS posts_keyword_idx
ON seo_agent.posts (primary_keyword_id, primary_keyword);

CREATE INDEX IF NOT EXISTS serp_keyword_locale_idx
ON seo_agent.serp_snapshots (normalized_keyword, google_gl, google_hl, requested_at DESC);

CREATE INDEX IF NOT EXISTS serp_keyword_id_idx
ON seo_agent.serp_snapshots (keyword_id, requested_at DESC);

CREATE INDEX IF NOT EXISTS tasks_status_priority_idx
ON seo_agent.tasks (status, priority, created_at);

CREATE INDEX IF NOT EXISTS tasks_site_keyword_idx
ON seo_agent.tasks (site_id, keyword_id, task_type);

CREATE INDEX IF NOT EXISTS articles_site_status_idx
ON seo_agent.articles (site_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS articles_keyword_idx
ON seo_agent.articles (keyword_id, status);

CREATE INDEX IF NOT EXISTS keywords_raw_gin_idx
ON seo_agent.keywords USING gin (raw);

CREATE INDEX IF NOT EXISTS posts_raw_gin_idx
ON seo_agent.posts USING gin (raw);

CREATE INDEX IF NOT EXISTS tasks_payload_gin_idx
ON seo_agent.tasks USING gin (payload);

CREATE INDEX IF NOT EXISTS articles_parts_gin_idx
ON seo_agent.articles USING gin (article_parts);
