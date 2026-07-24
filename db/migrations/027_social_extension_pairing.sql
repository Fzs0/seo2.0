-- Local Hubstudio extension pairing and resumable task delivery.
-- Pairing codes and device bearer tokens are stored only as salted hashes.
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
  ADD COLUMN IF NOT EXISTS extension_device_id uuid,
  ADD COLUMN IF NOT EXISTS extension_stage text,
  ADD COLUMN IF NOT EXISTS extension_claimed_at timestamptz;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'publish_jobs_extension_device_fk'
  ) THEN
    ALTER TABLE social.publish_jobs
      ADD CONSTRAINT publish_jobs_extension_device_fk
      FOREIGN KEY (extension_device_id)
      REFERENCES social.extension_devices(id) ON DELETE SET NULL;
  END IF;
END
$$;

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

CREATE INDEX IF NOT EXISTS extension_devices_container_idx
  ON social.extension_devices (business_id, container_code)
  WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS extension_jobs_device_stage_idx
  ON social.publish_jobs (extension_device_id, extension_stage, created_at);

COMMENT ON TABLE social.extension_devices IS
  'Locally paired Hubstudio browser extensions; stores only hashed bearer tokens.';
COMMENT ON TABLE social.extension_task_events IS
  'Append-only stage history reported by a paired Hubstudio extension.';
