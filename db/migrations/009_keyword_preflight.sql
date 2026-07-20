-- 通用 Semrush 关键词字段与导入预筛结果。
ALTER TABLE seo_agent.keywords ADD COLUMN IF NOT EXISTS trend numeric(8,2);
ALTER TABLE seo_agent.keywords ADD COLUMN IF NOT EXISTS pkd numeric(6,2);
ALTER TABLE seo_agent.keywords ADD COLUMN IF NOT EXISTS potential_traffic numeric(12,2);
ALTER TABLE seo_agent.keywords ADD COLUMN IF NOT EXISTS competitive_density numeric(6,4);
ALTER TABLE seo_agent.keywords ADD COLUMN IF NOT EXISTS serp_results integer;
ALTER TABLE seo_agent.keywords ADD COLUMN IF NOT EXISTS keyword_type text;
ALTER TABLE seo_agent.keywords ADD COLUMN IF NOT EXISTS preflight_status text NOT NULL DEFAULT 'ready';
ALTER TABLE seo_agent.keywords ADD COLUMN IF NOT EXISTS preflight_reason text;
ALTER TABLE seo_agent.keywords ADD COLUMN IF NOT EXISTS business_id text;
ALTER TABLE seo_agent.keywords ADD COLUMN IF NOT EXISTS source_batch_id text;

CREATE INDEX IF NOT EXISTS keywords_preflight_idx
  ON seo_agent.keywords (preflight_status, keyword_type, score DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS keywords_source_batch_idx
  ON seo_agent.keywords (source_batch_id, business_id);
