-- Read-only, versioned custom JSON connectors and canonical product SEO fields.
-- V1 supports products.list only; no external write capability is stored here.

SET search_path = seo_agent;

CREATE TABLE IF NOT EXISTS seo_agent.custom_connectors (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id uuid NOT NULL REFERENCES seo_agent.sites(id) ON DELETE CASCADE,
  name text NOT NULL,
  capability text NOT NULL CHECK (capability IN ('products.list')),
  status text NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'verified', 'active', 'disabled', 'schema_changed')),
  current_version integer NOT NULL DEFAULT 1 CHECK (current_version > 0),
  active_version integer,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (site_id, name)
);

CREATE TABLE IF NOT EXISTS seo_agent.custom_connector_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  connector_id uuid NOT NULL REFERENCES seo_agent.custom_connectors(id) ON DELETE CASCADE,
  version integer NOT NULL CHECK (version > 0),
  config jsonb NOT NULL,
  schema_fingerprint text,
  verified_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (connector_id, version)
);

CREATE TABLE IF NOT EXISTS seo_agent.custom_connector_secrets (
  connector_id uuid PRIMARY KEY REFERENCES seo_agent.custom_connectors(id) ON DELETE CASCADE,
  encrypted_value bytea NOT NULL,
  secret_names text[] NOT NULL DEFAULT ARRAY[]::text[],
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS seo_agent.custom_connector_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  connector_id uuid NOT NULL REFERENCES seo_agent.custom_connectors(id) ON DELETE CASCADE,
  version integer NOT NULL,
  run_type text NOT NULL CHECK (run_type IN ('test', 'sync')),
  status text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
  items_received integer NOT NULL DEFAULT 0,
  items_mapped integer NOT NULL DEFAULT 0,
  items_rejected integer NOT NULL DEFAULT 0,
  items_upserted integer NOT NULL DEFAULT 0,
  schema_fingerprint text,
  summary jsonb NOT NULL DEFAULT '{}'::jsonb,
  error_summary text,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);

CREATE INDEX IF NOT EXISTS custom_connectors_site_status_idx
  ON seo_agent.custom_connectors (site_id, status);
CREATE INDEX IF NOT EXISTS custom_connector_runs_connector_started_idx
  ON seo_agent.custom_connector_runs (connector_id, started_at DESC);

DROP TRIGGER IF EXISTS set_custom_connectors_updated_at ON seo_agent.custom_connectors;
CREATE TRIGGER set_custom_connectors_updated_at
BEFORE UPDATE ON seo_agent.custom_connectors
FOR EACH ROW EXECUTE FUNCTION seo_agent.set_updated_at();

ALTER TABLE seo_agent.products
  ADD COLUMN IF NOT EXISTS source_connector_id uuid REFERENCES seo_agent.custom_connectors(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS canonical_url text,
  ADD COLUMN IF NOT EXISTS meta_title text,
  ADD COLUMN IF NOT EXISTS meta_description text,
  ADD COLUMN IF NOT EXISTS meta_keywords text[] NOT NULL DEFAULT ARRAY[]::text[],
  ADD COLUMN IF NOT EXISTS images jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS variants jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS seo_audit jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS source_updated_at timestamptz;

ALTER TABLE seo_agent.products DROP CONSTRAINT IF EXISTS products_external_id_uk;
CREATE UNIQUE INDEX IF NOT EXISTS products_connector_external_id_uk
  ON seo_agent.products (source_connector_id, external_id)
  WHERE source_connector_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS products_legacy_external_id_uk
  ON seo_agent.products (external_id)
  WHERE source_connector_id IS NULL;

COMMENT ON TABLE seo_agent.custom_connectors IS
  'User-configured read-only JSON connectors. External writes are intentionally unsupported.';
COMMENT ON COLUMN seo_agent.custom_connector_versions.config IS
  'Versioned request/response/mapping contract without plaintext credentials.';
COMMENT ON COLUMN seo_agent.products.seo_audit IS
  'Deterministic product SEO gaps such as missing TDK, canonical URL, description, or image ALT.';
