-- Versioned structured analysis for synced posts.
CREATE TABLE IF NOT EXISTS seo_agent.post_analyses (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  post_id uuid NOT NULL REFERENCES seo_agent.posts(id) ON DELETE CASCADE,
  content_hash text NOT NULL,
  analysis jsonb NOT NULL,
  analyzed_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE seo_agent.post_analyses
  DROP CONSTRAINT IF EXISTS post_analyses_post_id_content_hash_key;

CREATE INDEX IF NOT EXISTS post_analyses_latest_idx
  ON seo_agent.post_analyses (post_id, analyzed_at DESC);

CREATE INDEX IF NOT EXISTS post_analyses_hash_idx
  ON seo_agent.post_analyses (post_id, content_hash);

COMMENT ON TABLE seo_agent.post_analyses IS '已同步文章的结构化分析历史；内容或 SEO 元数据变化时产生新版本。';
