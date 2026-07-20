-- Structured, user-confirmable content knowledge for each site.
ALTER TABLE seo_agent.sites
  ADD COLUMN IF NOT EXISTS knowledge_profile jsonb NOT NULL DEFAULT '{}'::jsonb;
