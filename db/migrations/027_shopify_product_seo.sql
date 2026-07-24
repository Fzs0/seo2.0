SET search_path = seo_agent;

DROP INDEX IF EXISTS seo_agent.products_legacy_external_id_uk;
CREATE UNIQUE INDEX IF NOT EXISTS products_site_external_id_uk
  ON seo_agent.products (site_id, external_id)
  WHERE source_connector_id IS NULL AND site_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS products_unscoped_external_id_uk
  ON seo_agent.products (external_id)
  WHERE source_connector_id IS NULL AND site_id IS NULL;

CREATE TABLE IF NOT EXISTS seo_agent.shopify_product_seo_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id uuid NOT NULL REFERENCES seo_agent.sites(id) ON DELETE CASCADE,
  product_id bigint REFERENCES seo_agent.products(id) ON DELETE SET NULL,
  external_id text NOT NULL,
  request_id uuid UNIQUE,
  run_type text NOT NULL CHECK (run_type IN ('sync', 'preview', 'update')),
  status text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'stale', 'unknown')),
  before_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  requested_patch jsonb NOT NULL DEFAULT '{}'::jsonb,
  after_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  error_summary text,
  actor text NOT NULL DEFAULT 'local_user',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS shopify_product_seo_runs_site_created_idx
  ON seo_agent.shopify_product_seo_runs (site_id, created_at DESC);

COMMENT ON TABLE seo_agent.shopify_product_seo_runs IS
  'Auditable Shopify product SEO previews and writes. Requested patches contain only SEO title and description.';
