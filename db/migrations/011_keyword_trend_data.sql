ALTER TABLE seo_agent.keywords ADD COLUMN IF NOT EXISTS trend_data jsonb NOT NULL DEFAULT '[]'::jsonb;
