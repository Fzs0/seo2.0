-- Stable topic-cluster metadata for pillar pages and supporting articles.
ALTER TABLE seo_agent.keywords ADD COLUMN IF NOT EXISTS topic_cluster_id text;
ALTER TABLE seo_agent.keywords ADD COLUMN IF NOT EXISTS cluster_role text;
ALTER TABLE seo_agent.keywords ADD COLUMN IF NOT EXISTS cluster_size integer;
ALTER TABLE seo_agent.keywords ADD COLUMN IF NOT EXISTS pillar_keyword text;

CREATE INDEX IF NOT EXISTS idx_keywords_topic_cluster_id
  ON seo_agent.keywords (topic_cluster_id);
CREATE INDEX IF NOT EXISTS idx_keywords_cluster_role
  ON seo_agent.keywords (cluster_role);
