-- Audited OEMApps product SEO writes. Every execution is single-product and snapshot guarded.

SET search_path = seo_agent;

CREATE TABLE IF NOT EXISTS seo_agent.product_seo_update_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  connector_id uuid NOT NULL REFERENCES seo_agent.custom_connectors(id) ON DELETE CASCADE,
  product_external_id text NOT NULL,
  status text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'verification_failed')),
  requested_patch jsonb NOT NULL,
  approved_changes jsonb NOT NULL DEFAULT '[]'::jsonb,
  expected_snapshot_hash text NOT NULL,
  before_snapshot jsonb NOT NULL,
  after_snapshot jsonb,
  variant_ids_before jsonb NOT NULL DEFAULT '[]'::jsonb,
  variant_ids_after jsonb,
  error_summary text,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);

CREATE INDEX IF NOT EXISTS product_seo_update_runs_connector_started_idx
  ON seo_agent.product_seo_update_runs (connector_id, started_at DESC);
CREATE INDEX IF NOT EXISTS product_seo_update_runs_product_idx
  ON seo_agent.product_seo_update_runs (connector_id, product_external_id, started_at DESC);

COMMENT ON TABLE seo_agent.product_seo_update_runs IS
  'Audit trail for explicit, snapshot-guarded OEMApps product SEO PUT operations.';
