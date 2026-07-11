#!/usr/bin/env node
// 重新灌 v2 baseline（包含 contentPlan 模板）。
// 用 stdin 把 SQL 喂给 docker exec 内的 psql，避开 ENAMETOOLONG。

import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";

const path = process.env.STANDARD_JSON_PATH || "workflows/seo-standard.json";
const standard = JSON.parse(readFileSync(path, "utf-8"));
const version = standard.version;
const payloadB64 = Buffer.from(JSON.stringify(standard), "utf-8").toString("base64");
const notes = `Manual reload with contentPlan template at ${new Date().toISOString()}`;

const sql = `
INSERT INTO seo_agent.rule_sets
  (name, version, source, payload, is_active, effective_at, created_by, notes)
VALUES
  ('seo-standard', '${version.replace(/'/g, "''")}', 'file',
   convert_from(decode('${payloadB64}', 'base64'), 'utf-8')::jsonb,
   true, now(), 'manual:reload', '${notes.replace(/'/g, "''")}')
ON CONFLICT (name, version) DO UPDATE SET
  payload = EXCLUDED.payload,
  notes = EXCLUDED.notes,
  updated_at = now()
RETURNING id, name, version, is_active, effective_at, octet_length(payload::text) AS bytes;
`.trim();

const r = spawnSync(
  "docker",
  [
    "exec", "-i", "-e", "PGPASSWORD=seo_dev_local", "pg-workbench",
    "psql", "-U", "seo", "-d", "seo_workbench", "-v", "ON_ERROR_STOP=1",
    "-t", "-A", "-X",
  ],
  { input: sql, encoding: "utf-8" }
);
if (r.status !== 0) {
  console.error("FAILED:", r.stderr || r.stdout);
  process.exit(1);
}
console.log("OK:", r.stdout.trim());