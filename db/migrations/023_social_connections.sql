-- Encrypted, test-before-enable social platform connections.
SET search_path = seo_agent;

CREATE TABLE IF NOT EXISTS seo_agent.social_connections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id text NOT NULL,
  platform text NOT NULL CHECK (platform IN ('x', 'reddit')),
  name text NOT NULL,
  config jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'verified', 'failed', 'disabled')),
  capabilities text[] NOT NULL DEFAULT ARRAY[]::text[],
  account_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  last_tested_at timestamptz,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, business_id),
  UNIQUE (business_id, platform, name)
);

CREATE TABLE IF NOT EXISTS seo_agent.social_connection_secrets (
  connection_id uuid PRIMARY KEY REFERENCES seo_agent.social_connections(id) ON DELETE CASCADE,
  encrypted_value bytea NOT NULL,
  secret_names text[] NOT NULL DEFAULT ARRAY[]::text[],
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS seo_agent.social_connection_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id text NOT NULL,
  connection_id uuid NOT NULL,
  status text NOT NULL CHECK (status IN ('succeeded', 'failed')),
  summary jsonb NOT NULL DEFAULT '{}'::jsonb,
  error_summary text,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (connection_id, business_id)
    REFERENCES seo_agent.social_connections(id, business_id) ON DELETE CASCADE
);

ALTER TABLE seo_agent.social_account_bindings
  ADD COLUMN IF NOT EXISTS connection_id uuid REFERENCES seo_agent.social_connections(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS social_connections_business_platform_idx
  ON seo_agent.social_connections (business_id, platform, status);
CREATE INDEX IF NOT EXISTS social_connection_runs_connection_started_idx
  ON seo_agent.social_connection_runs (connection_id, started_at DESC);

DROP TRIGGER IF EXISTS set_social_connections_updated_at ON seo_agent.social_connections;
CREATE TRIGGER set_social_connections_updated_at BEFORE UPDATE ON seo_agent.social_connections
FOR EACH ROW EXECUTE FUNCTION seo_agent.set_updated_at();

COMMENT ON COLUMN seo_agent.social_connections.config IS 'Non-secret platform configuration only.';
COMMENT ON TABLE seo_agent.social_connection_secrets IS 'Encrypted credentials; values are never returned by the API.';
