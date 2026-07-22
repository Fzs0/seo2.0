-- Social publishing foundation. No external publishing or account passwords are enabled here.
SET search_path = seo_agent;

CREATE TABLE IF NOT EXISTS seo_agent.social_decisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id text NOT NULL,
  topic text NOT NULL,
  language_code text NOT NULL,
  requested_platforms text[] NOT NULL DEFAULT ARRAY[]::text[],
  status text NOT NULL CHECK (status IN ('allowed', 'blocked', 'manual_review')),
  decision jsonb NOT NULL,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by text NOT NULL DEFAULT 'api',
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, business_id)
);

CREATE TABLE IF NOT EXISTS seo_agent.social_content_packages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id text NOT NULL,
  decision_id uuid NOT NULL,
  platform text NOT NULL,
  content_type text NOT NULL,
  status text NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'in_review', 'approved', 'rejected', 'queued', 'published', 'cancelled')),
  current_version integer NOT NULL DEFAULT 1 CHECK (current_version > 0),
  review_note text,
  reviewed_by text,
  reviewed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, business_id),
  UNIQUE (decision_id, platform),
  FOREIGN KEY (decision_id, business_id)
    REFERENCES seo_agent.social_decisions(id, business_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS seo_agent.social_content_package_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id text NOT NULL,
  package_id uuid NOT NULL,
  version integer NOT NULL CHECK (version > 0),
  language_code text NOT NULL,
  title text NOT NULL DEFAULT '',
  body text NOT NULL,
  media jsonb NOT NULL DEFAULT '[]'::jsonb,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  risk_score numeric(5,2) NOT NULL DEFAULT 0 CHECK (risk_score >= 0 AND risk_score <= 100),
  content_hash text NOT NULL,
  created_by text NOT NULL DEFAULT 'api',
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, business_id),
  UNIQUE (package_id, version),
  UNIQUE (package_id, content_hash),
  FOREIGN KEY (package_id, business_id)
    REFERENCES seo_agent.social_content_packages(id, business_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS seo_agent.social_account_bindings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id text NOT NULL,
  platform text NOT NULL,
  display_name text NOT NULL,
  container_code text NOT NULL DEFAULT '',
  container_name text NOT NULL DEFAULT '',
  binding_name text NOT NULL,
  default_publish_url text NOT NULL CHECK (default_publish_url ~ '^https://'),
  delivery_channel text NOT NULL DEFAULT 'hubstudio_browser',
  publish_adapter text NOT NULL,
  enabled boolean NOT NULL DEFAULT false,
  login_status text NOT NULL DEFAULT 'unknown'
    CHECK (login_status IN ('unknown', 'valid', 'expired', 'manual_required')),
  last_tested_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, business_id),
  UNIQUE (business_id, platform, container_code, binding_name)
);

CREATE TABLE IF NOT EXISTS seo_agent.social_publish_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id text NOT NULL,
  package_id uuid NOT NULL,
  package_version_id uuid NOT NULL,
  binding_id uuid NOT NULL,
  idempotency_key text NOT NULL,
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'opening_environment', 'connecting_browser', 'filling_content',
      'awaiting_review', 'publishing', 'success', 'failed', 'manual_required', 'cancelled')),
  error_code text,
  error_message text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, business_id),
  UNIQUE (business_id, idempotency_key),
  FOREIGN KEY (package_id, business_id)
    REFERENCES seo_agent.social_content_packages(id, business_id) ON DELETE RESTRICT,
  FOREIGN KEY (package_version_id, business_id)
    REFERENCES seo_agent.social_content_package_versions(id, business_id) ON DELETE RESTRICT,
  FOREIGN KEY (binding_id, business_id)
    REFERENCES seo_agent.social_account_bindings(id, business_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS seo_agent.social_publish_attempts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id text NOT NULL,
  job_id uuid NOT NULL,
  attempt_no integer NOT NULL CHECK (attempt_no > 0),
  status text NOT NULL CHECK (status IN ('started', 'prepared', 'awaiting_review', 'publishing',
    'succeeded', 'failed', 'manual_required', 'cancelled')),
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  error_code text,
  error_message text,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  UNIQUE (job_id, attempt_no),
  FOREIGN KEY (job_id, business_id)
    REFERENCES seo_agent.social_publish_jobs(id, business_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS seo_agent.social_posts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id text NOT NULL,
  job_id uuid NOT NULL,
  package_version_id uuid NOT NULL,
  binding_id uuid NOT NULL,
  platform text NOT NULL,
  remote_post_id text,
  remote_url text NOT NULL CHECK (remote_url ~ '^https://'),
  status text NOT NULL DEFAULT 'published'
    CHECK (status IN ('published', 'unverified', 'removed', 'unknown')),
  remote_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  published_at timestamptz NOT NULL,
  last_verified_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, business_id),
  UNIQUE (job_id),
  UNIQUE (remote_url),
  FOREIGN KEY (job_id, business_id)
    REFERENCES seo_agent.social_publish_jobs(id, business_id) ON DELETE RESTRICT,
  FOREIGN KEY (package_version_id, business_id)
    REFERENCES seo_agent.social_content_package_versions(id, business_id) ON DELETE RESTRICT,
  FOREIGN KEY (binding_id, business_id)
    REFERENCES seo_agent.social_account_bindings(id, business_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS social_posts_remote_id_idx
  ON seo_agent.social_posts (binding_id, remote_post_id) WHERE remote_post_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS seo_agent.social_metric_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id text NOT NULL,
  post_id uuid NOT NULL,
  captured_at timestamptz NOT NULL,
  source text NOT NULL,
  metrics jsonb NOT NULL,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (post_id, captured_at, source),
  FOREIGN KEY (post_id, business_id)
    REFERENCES seo_agent.social_posts(id, business_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS social_decisions_business_created_idx ON seo_agent.social_decisions (business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS social_packages_business_status_idx ON seo_agent.social_content_packages (business_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS social_bindings_business_platform_idx ON seo_agent.social_account_bindings (business_id, platform, enabled);
CREATE INDEX IF NOT EXISTS social_jobs_business_status_idx ON seo_agent.social_publish_jobs (business_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS social_metrics_post_captured_idx ON seo_agent.social_metric_snapshots (post_id, captured_at DESC);

DROP TRIGGER IF EXISTS set_social_content_packages_updated_at ON seo_agent.social_content_packages;
CREATE TRIGGER set_social_content_packages_updated_at BEFORE UPDATE ON seo_agent.social_content_packages
FOR EACH ROW EXECUTE FUNCTION seo_agent.set_updated_at();
DROP TRIGGER IF EXISTS set_social_account_bindings_updated_at ON seo_agent.social_account_bindings;
CREATE TRIGGER set_social_account_bindings_updated_at BEFORE UPDATE ON seo_agent.social_account_bindings
FOR EACH ROW EXECUTE FUNCTION seo_agent.set_updated_at();
DROP TRIGGER IF EXISTS set_social_publish_jobs_updated_at ON seo_agent.social_publish_jobs;
CREATE TRIGGER set_social_publish_jobs_updated_at BEFORE UPDATE ON seo_agent.social_publish_jobs
FOR EACH ROW EXECUTE FUNCTION seo_agent.set_updated_at();

COMMENT ON TABLE seo_agent.social_content_package_versions IS 'Immutable platform-specific content versions.';
COMMENT ON TABLE seo_agent.social_account_bindings IS 'Hubstudio environment bindings; never stores platform passwords.';
COMMENT ON TABLE seo_agent.social_posts IS 'Remote publication truth; a job cannot be considered successful without a verified row.';
