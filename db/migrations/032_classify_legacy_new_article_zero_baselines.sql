-- Older publication scripts correctly used a page-level zero baseline for a
-- URL that did not exist before publication, but did not persist the explicit
-- baseline kind. Classify only records with strong, self-contained evidence.
--
-- The original payload is backed up by migration 031's shared repair table.
CREATE TABLE IF NOT EXISTS seo_agent.strategy_effect_baseline_repair_backup (
  effect_task_id uuid PRIMARY KEY,
  original_target_url text,
  original_payload jsonb NOT NULL,
  original_decision jsonb,
  original_updated_at timestamptz,
  backed_up_at timestamptz NOT NULL DEFAULT now()
);

-- Backups are historical evidence. They must survive, and must never prevent,
-- deletion of the mutable operational task that they describe.
DO $$
DECLARE
  constraint_name name;
BEGIN
  FOR constraint_name IN
    SELECT conname
      FROM pg_constraint
     WHERE conrelid =
           'seo_agent.strategy_effect_baseline_repair_backup'::regclass
       AND contype = 'f'
       AND confrelid = 'seo_agent.tasks'::regclass
  LOOP
    EXECUTE format(
      'ALTER TABLE seo_agent.strategy_effect_baseline_repair_backup DROP CONSTRAINT %I',
      constraint_name
    );
  END LOOP;
END;
$$;

WITH candidates AS (
  SELECT id,
         target_url,
         payload,
         decision,
         updated_at
    FROM seo_agent.tasks
   WHERE task_type = 'review'
     AND payload->>'kind' = 'strategy_effect'
     AND payload->>'action' = 'new_article'
     AND payload#>>'{baseline,kind}' IS NULL
     AND NULLIF(payload->>'published_at', '') IS NOT NULL
     AND COALESCE(NULLIF(target_url, ''), NULLIF(payload->>'target_url', '')) IS NOT NULL
     AND payload#>>'{baseline,metric_scope,gsc}' = 'page'
     AND payload#>>'{baseline,metric_scope,ga4}' = 'landing_page'
     AND payload#>'{baseline,gsc}' @> '{"clicks": 0, "impressions": 0, "avg_position": 0}'::jsonb
     AND payload#>'{baseline,ga4}' @> '{"sessions": 0, "conversions": 0}'::jsonb
     AND payload->>'baseline_note' ILIKE 'New%URL did not exist before publication%'
)
INSERT INTO seo_agent.strategy_effect_baseline_repair_backup
  (effect_task_id, original_target_url, original_payload, original_decision, original_updated_at)
SELECT id, target_url, payload, decision, updated_at
  FROM candidates
ON CONFLICT (effect_task_id) DO NOTHING;

UPDATE seo_agent.tasks
   SET payload = jsonb_set(
                   jsonb_set(
                     jsonb_set(
                       payload,
                       '{baseline,kind}',
                       to_jsonb('structural_zero'::text),
                       true
                     ),
                     '{baseline,target_url}',
                     to_jsonb(COALESCE(NULLIF(target_url, ''), payload->>'target_url')),
                     true
                   ),
                   '{baseline,effective_at}',
                   to_jsonb(payload->>'published_at'),
                   true
                 )
                 || jsonb_build_object('baseline_valid', true),
       updated_at = now()
 WHERE task_type = 'review'
   AND payload->>'kind' = 'strategy_effect'
   AND payload->>'action' = 'new_article'
   AND payload#>>'{baseline,kind}' IS NULL
   AND NULLIF(payload->>'published_at', '') IS NOT NULL
   AND COALESCE(NULLIF(target_url, ''), NULLIF(payload->>'target_url', '')) IS NOT NULL
   AND payload#>>'{baseline,metric_scope,gsc}' = 'page'
   AND payload#>>'{baseline,metric_scope,ga4}' = 'landing_page'
   AND payload#>'{baseline,gsc}' @> '{"clicks": 0, "impressions": 0, "avg_position": 0}'::jsonb
   AND payload#>'{baseline,ga4}' @> '{"sessions": 0, "conversions": 0}'::jsonb
   AND payload->>'baseline_note' ILIKE 'New%URL did not exist before publication%';
