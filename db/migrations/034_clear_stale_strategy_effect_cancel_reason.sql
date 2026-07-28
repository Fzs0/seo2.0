-- A strategy effect can be canceled before publication and later reactivated
-- after exact remote readback confirms the write. Its audit events retain the
-- cancellation history; the mutable current-state task must not continue to
-- advertise a cancellation reason once it is active again.

UPDATE seo_agent.tasks
   SET payload = COALESCE(payload, '{}'::jsonb) - 'canceled_reason',
       decision = COALESCE(decision, '{}'::jsonb) - 'canceled_reason',
       updated_at = now()
 WHERE task_type = 'review'
   AND payload->>'kind' = 'strategy_effect'
   AND status IN ('queued', 'running', 'done')
   AND (
     COALESCE(payload, '{}'::jsonb) ? 'canceled_reason'
     OR COALESCE(decision, '{}'::jsonb) ? 'canceled_reason'
   );
