-- Canonical homepage SEO state and audited OEMApps homepage SEO writes.

SET search_path = seo_agent;

CREATE TABLE IF NOT EXISTS seo_agent.site_home_seo (
  id bigserial PRIMARY KEY,
  site_id uuid NOT NULL REFERENCES seo_agent.sites(id) ON DELETE CASCADE,
  source_connector_id uuid NOT NULL UNIQUE REFERENCES seo_agent.custom_connectors(id) ON DELETE CASCADE,
  meta_title text,
  meta_description text,
  meta_keywords text[] NOT NULL DEFAULT ARRAY[]::text[],
  seo_audit jsonb NOT NULL DEFAULT '{}'::jsonb,
  extracted_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS seo_agent.home_seo_update_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  connector_id uuid NOT NULL REFERENCES seo_agent.custom_connectors(id) ON DELETE CASCADE,
  status text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'verification_failed')),
  requested_patch jsonb NOT NULL,
  approved_changes jsonb NOT NULL DEFAULT '[]'::jsonb,
  expected_snapshot_hash text NOT NULL,
  before_snapshot jsonb NOT NULL,
  after_snapshot jsonb,
  error_summary text,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);

CREATE INDEX IF NOT EXISTS home_seo_update_runs_connector_started_idx
  ON seo_agent.home_seo_update_runs (connector_id, started_at DESC);

COMMENT ON TABLE seo_agent.site_home_seo IS
  'Canonical homepage TDK synchronized from an OEMApps self-hosted site.';
COMMENT ON TABLE seo_agent.home_seo_update_runs IS
  'Audit trail for snapshot-guarded OEMApps homepage SEO PUT operations.';
