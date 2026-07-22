SET search_path = seo_agent;

ALTER TABLE seo_agent.social_publish_jobs
  ADD COLUMN IF NOT EXISTS executor_confirmation bytea,
  ADD COLUMN IF NOT EXISTS executor_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS prepared_at timestamptz;

COMMENT ON COLUMN seo_agent.social_publish_jobs.executor_confirmation IS
  'Encrypted, short-lived executor confirmation token; never returned after prepare.';
