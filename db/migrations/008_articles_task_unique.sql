CREATE UNIQUE INDEX IF NOT EXISTS articles_task_unique
ON seo_agent.articles (task_id)
WHERE task_id IS NOT NULL;
