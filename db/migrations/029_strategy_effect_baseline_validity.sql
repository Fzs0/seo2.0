-- Effects backfilled without an original page URL cannot produce a reliable
-- before/after comparison for update_article strategies.
--
-- Idempotent: rows already marked invalid are left unchanged.
UPDATE seo_agent.tasks
   SET payload = jsonb_set(payload, '{baseline_valid}', 'false'::jsonb, true),
       updated_at = now()
 WHERE task_type = 'review'
   AND payload->>'kind' = 'strategy_effect'
   AND payload->>'action' = 'update_article'
   AND payload->>'baseline_valid' IS DISTINCT FROM 'false'
   AND payload->>'baseline_note' LIKE '初始基线创建时未绑定目标 URL%';
