-- Older clients persisted SERP failures before structured error metadata was
-- available. Keep the original payload, but classify these rows explicitly so
-- monitoring does not confuse "unknown historical cause" with a current
-- provider, credential, or quota incident.
--
-- Idempotent: only fetch-failed rows without an error_type are updated.
UPDATE seo_agent.serp_snapshots
   SET raw = raw
       || jsonb_build_object(
            'error_type', 'legacy_unclassified',
            'retryable', false,
            'classification_source', 'migration_030'
          )
 WHERE raw->>'status' = 'fetch-failed'
   AND COALESCE(raw->>'error_type', '') = '';
