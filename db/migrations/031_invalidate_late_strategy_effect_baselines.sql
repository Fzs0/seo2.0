-- A measured baseline captured at or after publication is not a pre-publication
-- baseline. Preserve the original evidence, then force the effect outcome to
-- inconclusive. No metric values are deleted or recalculated.
--
-- Idempotent: the backup uses the effect task ID as its primary key and rows
-- already marked invalid are left unchanged.
CREATE TABLE IF NOT EXISTS seo_agent.strategy_effect_baseline_repair_backup (
  effect_task_id uuid PRIMARY KEY,
  original_target_url text,
  original_payload jsonb NOT NULL,
  original_decision jsonb,
  original_updated_at timestamptz,
  backed_up_at timestamptz NOT NULL DEFAULT now()
);

-- The archive must outlive operational tasks and must not block their deletion.
-- Remove the constraint as well when this migration repairs a table created by
-- an earlier draft.
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

-- PostgreSQL 14 has no pg_input_is_valid. A session-local conversion
-- helper safely maps null, empty, malformed, out-of-range and otherwise
-- unparseable values to NULL without leaving any persistent database function.
CREATE OR REPLACE FUNCTION pg_temp.strategy_effect_try_timestamptz(_value text)
RETURNS timestamptz
LANGUAGE plpgsql
STABLE
AS $$
BEGIN
  IF NULLIF(btrim(_value), '') IS NULL THEN
    RETURN NULL;
  END IF;
  RETURN _value::timestamptz;
EXCEPTION
  WHEN OTHERS THEN
    RETURN NULL;
END;
$$;

WITH parsed_candidates AS (
  SELECT id,
         target_url,
         payload,
         decision,
         updated_at,
         pg_temp.strategy_effect_try_timestamptz(
           payload#>>'{baseline,captured_at}'
         ) AS captured_at,
         pg_temp.strategy_effect_try_timestamptz(
           payload->>'published_at'
         ) AS published_at
    FROM seo_agent.tasks
   WHERE task_type = 'review'
     AND payload->>'kind' = 'strategy_effect'
     AND payload->>'action' = 'update_article'
     AND payload->>'baseline_valid' IS DISTINCT FROM 'false'
), candidates AS (
  SELECT id, target_url, payload, decision, updated_at
    FROM parsed_candidates
   WHERE captured_at IS NOT NULL
     AND published_at IS NOT NULL
     AND isfinite(captured_at)
     AND isfinite(published_at)
     AND captured_at >= published_at
)
INSERT INTO seo_agent.strategy_effect_baseline_repair_backup
  (effect_task_id, original_target_url, original_payload, original_decision, original_updated_at)
SELECT id, target_url, payload, decision, updated_at
  FROM candidates
ON CONFLICT (effect_task_id) DO NOTHING;

WITH parsed_candidates AS (
  SELECT id,
         pg_temp.strategy_effect_try_timestamptz(
           payload#>>'{baseline,captured_at}'
         ) AS captured_at,
         pg_temp.strategy_effect_try_timestamptz(
           payload->>'published_at'
         ) AS published_at
    FROM seo_agent.tasks
   WHERE task_type = 'review'
     AND payload->>'kind' = 'strategy_effect'
     AND payload->>'action' = 'update_article'
     AND payload->>'baseline_valid' IS DISTINCT FROM 'false'
), candidates AS (
  SELECT id
    FROM parsed_candidates
   WHERE captured_at IS NOT NULL
     AND published_at IS NOT NULL
     AND isfinite(captured_at)
     AND isfinite(published_at)
     AND captured_at >= published_at
)
UPDATE seo_agent.tasks AS task
   SET payload = payload
                 || jsonb_build_object(
                      'baseline_valid', false,
                      'baseline_note',
                      '发布前基线的采集时间不早于文章发布时间；该数据可能在发布后产生，不能用于前后对比。',
                      'outcome', 'inconclusive'
                    ),
       decision = COALESCE(decision, '{}'::jsonb)
                  || jsonb_build_object(
                       'outcome', 'inconclusive',
                       'reason', 'late_baseline_capture'
                    ),
       updated_at = now()
  FROM candidates
 WHERE task.id = candidates.id;
