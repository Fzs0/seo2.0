CREATE SCHEMA IF NOT EXISTS social;

CREATE OR REPLACE FUNCTION social.set_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END
$$;

CREATE TABLE IF NOT EXISTS social.decisions (
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

CREATE TABLE IF NOT EXISTS social.content_packages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id text NOT NULL,
  decision_id uuid NOT NULL,
  platform text NOT NULL,
  content_type text NOT NULL,
  status text NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'in_review', 'approved', 'rejected', 'queued',
                      'published', 'cancelled')),
  current_version integer NOT NULL DEFAULT 1 CHECK (current_version > 0),
  review_note text,
  reviewed_by text,
  reviewed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, business_id),
  UNIQUE (decision_id, platform),
  FOREIGN KEY (decision_id, business_id)
    REFERENCES social.decisions(id, business_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS social.content_package_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id text NOT NULL,
  package_id uuid NOT NULL,
  version integer NOT NULL CHECK (version > 0),
  language_code text NOT NULL,
  title text NOT NULL DEFAULT '',
  body text NOT NULL,
  media jsonb NOT NULL DEFAULT '[]'::jsonb,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  risk_score numeric(5,2) NOT NULL DEFAULT 0
    CHECK (risk_score >= 0 AND risk_score <= 100),
  content_hash text NOT NULL,
  created_by text NOT NULL DEFAULT 'api',
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, business_id),
  UNIQUE (package_id, version),
  UNIQUE (package_id, content_hash),
  FOREIGN KEY (package_id, business_id)
    REFERENCES social.content_packages(id, business_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS social.connections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id text NOT NULL,
  platform text NOT NULL CHECK (
    platform IN ('x', 'reddit', 'quora', 'youtube', 'tiktok', 'facebook', 'instagram')
  ),
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

CREATE TABLE IF NOT EXISTS social.connection_secrets (
  connection_id uuid PRIMARY KEY
    REFERENCES social.connections(id) ON DELETE CASCADE,
  encrypted_value bytea NOT NULL,
  secret_names text[] NOT NULL DEFAULT ARRAY[]::text[],
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS social.connection_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id text NOT NULL,
  connection_id uuid NOT NULL,
  status text NOT NULL CHECK (status IN ('succeeded', 'failed')),
  summary jsonb NOT NULL DEFAULT '{}'::jsonb,
  error_summary text,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (connection_id, business_id)
    REFERENCES social.connections(id, business_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS social.account_bindings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id text NOT NULL,
  connection_id uuid NOT NULL REFERENCES social.connections(id) ON DELETE RESTRICT,
  platform text NOT NULL,
  display_name text NOT NULL,
  container_code text NOT NULL,
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

CREATE TABLE IF NOT EXISTS social.publish_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id text NOT NULL,
  package_id uuid NOT NULL,
  package_version_id uuid NOT NULL,
  binding_id uuid NOT NULL,
  idempotency_key text NOT NULL,
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'opening_environment', 'connecting_browser',
      'filling_content', 'awaiting_review', 'publishing', 'success', 'failed',
      'manual_required', 'cancelled')),
  error_code text,
  error_message text,
  executor_confirmation bytea,
  executor_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  prepared_at timestamptz,
  extension_device_id uuid,
  extension_stage text,
  extension_claimed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, business_id),
  UNIQUE (business_id, idempotency_key),
  FOREIGN KEY (package_id, business_id)
    REFERENCES social.content_packages(id, business_id) ON DELETE RESTRICT,
  FOREIGN KEY (package_version_id, business_id)
    REFERENCES social.content_package_versions(id, business_id) ON DELETE RESTRICT,
  FOREIGN KEY (binding_id, business_id)
    REFERENCES social.account_bindings(id, business_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS social.publish_attempts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id text NOT NULL,
  job_id uuid NOT NULL,
  attempt_no integer NOT NULL CHECK (attempt_no > 0),
  status text NOT NULL CHECK (status IN ('started', 'prepared', 'awaiting_review',
    'publishing', 'succeeded', 'failed', 'manual_required', 'cancelled')),
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  error_code text,
  error_message text,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  UNIQUE (job_id, attempt_no),
  FOREIGN KEY (job_id, business_id)
    REFERENCES social.publish_jobs(id, business_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS social.posts (
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
    REFERENCES social.publish_jobs(id, business_id) ON DELETE RESTRICT,
  FOREIGN KEY (package_version_id, business_id)
    REFERENCES social.content_package_versions(id, business_id) ON DELETE RESTRICT,
  FOREIGN KEY (binding_id, business_id)
    REFERENCES social.account_bindings(id, business_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS social.metric_snapshots (
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
    REFERENCES social.posts(id, business_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS social.extension_pairing_codes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id text NOT NULL,
  container_code text NOT NULL,
  display_name text NOT NULL,
  code_hash text NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  consumed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS social.extension_devices (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id text NOT NULL,
  container_code text NOT NULL,
  display_name text NOT NULL,
  extension_instance_id text NOT NULL,
  token_hash text NOT NULL UNIQUE,
  last_seen_at timestamptz,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (business_id, container_code, extension_instance_id)
);

ALTER TABLE social.publish_jobs
  DROP CONSTRAINT IF EXISTS publish_jobs_extension_device_fk;
ALTER TABLE social.publish_jobs
  ADD CONSTRAINT publish_jobs_extension_device_fk
  FOREIGN KEY (extension_device_id)
  REFERENCES social.extension_devices(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS social.extension_task_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id text NOT NULL,
  job_id uuid NOT NULL,
  device_id uuid NOT NULL REFERENCES social.extension_devices(id) ON DELETE RESTRICT,
  sequence_no integer NOT NULL CHECK (sequence_no > 0),
  stage text NOT NULL,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (job_id, device_id, sequence_no),
  FOREIGN KEY (job_id, business_id)
    REFERENCES social.publish_jobs(id, business_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS decisions_business_created_idx
  ON social.decisions (business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS packages_business_status_idx
  ON social.content_packages (business_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS bindings_business_platform_idx
  ON social.account_bindings (business_id, platform, enabled);
CREATE INDEX IF NOT EXISTS jobs_business_status_idx
  ON social.publish_jobs (business_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS connection_runs_connection_started_idx
  ON social.connection_runs (connection_id, started_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS posts_remote_id_idx
  ON social.posts (binding_id, remote_post_id) WHERE remote_post_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS extension_devices_one_active_environment_idx
  ON social.extension_devices (business_id, container_code)
  WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS extension_jobs_device_stage_idx
  ON social.publish_jobs (extension_device_id, extension_stage, created_at);

DROP TRIGGER IF EXISTS set_content_packages_updated_at ON social.content_packages;
CREATE TRIGGER set_content_packages_updated_at
BEFORE UPDATE ON social.content_packages
FOR EACH ROW EXECUTE FUNCTION social.set_updated_at();
DROP TRIGGER IF EXISTS set_connections_updated_at ON social.connections;
CREATE TRIGGER set_connections_updated_at
BEFORE UPDATE ON social.connections
FOR EACH ROW EXECUTE FUNCTION social.set_updated_at();
DROP TRIGGER IF EXISTS set_account_bindings_updated_at ON social.account_bindings;
CREATE TRIGGER set_account_bindings_updated_at
BEFORE UPDATE ON social.account_bindings
FOR EACH ROW EXECUTE FUNCTION social.set_updated_at();
DROP TRIGGER IF EXISTS set_publish_jobs_updated_at ON social.publish_jobs;
CREATE TRIGGER set_publish_jobs_updated_at
BEFORE UPDATE ON social.publish_jobs
FOR EACH ROW EXECUTE FUNCTION social.set_updated_at();
