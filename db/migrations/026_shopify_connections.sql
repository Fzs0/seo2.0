SET search_path = seo_agent;

CREATE TABLE IF NOT EXISTS seo_agent.shopify_connections (
  site_id uuid PRIMARY KEY REFERENCES seo_agent.sites(id) ON DELETE CASCADE,
  shop_domain text NOT NULL UNIQUE,
  blog_handle text NOT NULL DEFAULT 'news',
  api_version text NOT NULL DEFAULT '2026-07',
  status text NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'verified', 'active', 'failed', 'disabled')),
  scopes text[] NOT NULL DEFAULT ARRAY[]::text[],
  credential_fingerprint text,
  last_tested_at timestamptz,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS seo_agent.shopify_connection_secrets (
  site_id uuid PRIMARY KEY REFERENCES seo_agent.shopify_connections(site_id) ON DELETE CASCADE,
  encrypted_value bytea NOT NULL,
  secret_names text[] NOT NULL DEFAULT ARRAY[]::text[],
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS seo_agent.shopify_connection_rollbacks (
  site_id uuid PRIMARY KEY REFERENCES seo_agent.shopify_connections(site_id) ON DELETE CASCADE,
  shop_domain text NOT NULL,
  blog_handle text NOT NULL,
  api_version text NOT NULL,
  scopes text[] NOT NULL,
  credential_fingerprint text,
  encrypted_value bytea NOT NULL,
  secret_names text[] NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS seo_agent.shopify_connection_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id uuid NOT NULL REFERENCES seo_agent.shopify_connections(site_id) ON DELETE CASCADE,
  run_type text NOT NULL CHECK (run_type IN ('test', 'activate', 'rotate')),
  status text NOT NULL CHECK (status IN ('succeeded', 'failed')),
  summary jsonb NOT NULL DEFAULT '{}'::jsonb,
  error_summary text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS shopify_connection_runs_site_created_idx
  ON seo_agent.shopify_connection_runs (site_id, created_at DESC);

DROP TRIGGER IF EXISTS set_shopify_connections_updated_at ON seo_agent.shopify_connections;
CREATE TRIGGER set_shopify_connections_updated_at
BEFORE UPDATE ON seo_agent.shopify_connections
FOR EACH ROW EXECUTE FUNCTION seo_agent.set_updated_at();

COMMENT ON TABLE seo_agent.shopify_connections IS
  'Per-site Shopify Admin configuration without plaintext credentials.';
COMMENT ON TABLE seo_agent.shopify_connection_secrets IS
  'Encrypted per-site Shopify client credentials; never returned by APIs.';
COMMENT ON TABLE seo_agent.shopify_connection_rollbacks IS
  'Last active Shopify configuration retained while a replacement candidate is verified.';
