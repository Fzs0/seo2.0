-- Canonical OEMApps product collections and audited collection SEO writes.

SET search_path = seo_agent;

CREATE TABLE IF NOT EXISTS seo_agent.product_collections (
  id bigserial PRIMARY KEY,
  site_id uuid NOT NULL REFERENCES seo_agent.sites(id) ON DELETE CASCADE,
  source_connector_id uuid NOT NULL REFERENCES seo_agent.custom_connectors(id) ON DELETE CASCADE,
  external_id text NOT NULL,
  title text NOT NULL,
  handle text,
  url text,
  top_description text,
  bottom_description text,
  meta_title text,
  meta_description text,
  meta_keywords text[] NOT NULL DEFAULT ARRAY[]::text[],
  image_url text,
  sort_order jsonb NOT NULL DEFAULT '{}'::jsonb,
  manual_mode integer NOT NULL DEFAULT 0,
  product_count integer NOT NULL DEFAULT 0,
  member_product_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  seo_audit jsonb NOT NULL DEFAULT '{}'::jsonb,
  source_updated_at timestamptz,
  extracted_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_connector_id, external_id)
);

CREATE INDEX IF NOT EXISTS product_collections_site_idx
  ON seo_agent.product_collections (site_id, title);

CREATE TABLE IF NOT EXISTS seo_agent.collection_seo_update_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  connector_id uuid NOT NULL REFERENCES seo_agent.custom_connectors(id) ON DELETE CASCADE,
  collection_external_id text NOT NULL,
  status text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'verification_failed')),
  requested_patch jsonb NOT NULL,
  approved_changes jsonb NOT NULL DEFAULT '[]'::jsonb,
  expected_snapshot_hash text NOT NULL,
  before_snapshot jsonb NOT NULL,
  after_snapshot jsonb,
  member_product_ids_before jsonb NOT NULL DEFAULT '[]'::jsonb,
  member_product_ids_after jsonb,
  error_summary text,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);

CREATE INDEX IF NOT EXISTS collection_seo_update_runs_connector_started_idx
  ON seo_agent.collection_seo_update_runs (connector_id, started_at DESC);

COMMENT ON TABLE seo_agent.product_collections IS
  'Canonical product collection inventory synchronized from OEMApps self-hosted sites.';
COMMENT ON TABLE seo_agent.collection_seo_update_runs IS
  'Audit trail for snapshot-guarded OEMApps collection SEO PUT operations.';
