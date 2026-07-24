-- Keep exactly one active extension connection for each Hubstudio environment.
-- Historical rows remain available for task/event audit through revoked_at.
WITH ranked_devices AS (
  SELECT id,
         row_number() OVER (
           PARTITION BY business_id, container_code
           ORDER BY last_seen_at DESC NULLS LAST, updated_at DESC, created_at DESC, id DESC
         ) AS active_rank
    FROM social.extension_devices
   WHERE revoked_at IS NULL
)
UPDATE social.extension_devices AS device
   SET revoked_at = now(), updated_at = now()
  FROM ranked_devices AS ranked
 WHERE device.id = ranked.id
   AND ranked.active_rank > 1;

CREATE UNIQUE INDEX IF NOT EXISTS extension_devices_one_active_environment_idx
  ON social.extension_devices (business_id, container_code)
  WHERE revoked_at IS NULL;
