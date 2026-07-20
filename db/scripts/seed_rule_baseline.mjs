#!/usr/bin/env node
// =====================================================================
// Seed 脚本（零 npm 依赖版）：把 workflows/seo-standard.json 灌入
// seo_agent.rule_sets 作为初始 baseline。
//
// 通过 docker exec 在容器内跑 psql，避免对 node_modules/pg 的依赖。
//
// 用法：
//   PG_CONTAINER=pg-workbench DB_USER=seo DB_PASS=seo_dev_local DB_NAME=seo_workbench \
//     node db/scripts/seed_rule_baseline.mjs
//
// 读：workflows/seo-standard.json（相对仓库根）
// 写：seo_agent.rule_sets 一行；seo_agent.rule_audit_log 两行
//
// 幂等：相同 JSON 版本重跑是 no-op。
// 保守：不会静默覆盖另一个来源已激活的版本。
// =====================================================================

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";

const RULE_FAMILY_NAME = "seo-standard";
const RULE_SOURCE = "file";
const ACTOR = "seed:db/scripts/seed_rule_baseline.mjs";

const PG_CONTAINER = process.env.PG_CONTAINER || "pg-workbench";
const DB_USER = process.env.DB_USER || "seo";
const DB_PASS = process.env.DB_PASS || "seo_dev_local";
const DB_NAME = process.env.DB_NAME || "seo_workbench";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..");
// 允许 STANDARD_JSON_PATH 覆盖；否则按以下顺序找：
//   1. ${repoRoot}/workflows/seo-standard.json
//   2. ${repoRoot}/../workflows/seo-standard.json（兼容 seo-2.0 与原 seo-workbench 同级）
function findStandardPath() {
  if (process.env.STANDARD_JSON_PATH) return process.env.STANDARD_JSON_PATH;
  const candidates = [
    join(repoRoot, "workflows", "seo-standard.json"),
    join(repoRoot, "..", "workflows", "seo-standard.json"),
  ];
  for (const p of candidates) {
    try {
      readFileSync(p, "utf8");
      return p;
    } catch {
      // 不存在，继续找下一个
    }
  }
  return null;  // 全部失败；让 main() 报完整路径列表
}
const standardPath = findStandardPath();

function log(stage, message, extra = {}) {
  const stamp = new Date().toISOString();
  const payload = Object.keys(extra).length ? ` ${JSON.stringify(extra)}` : "";
  process.stdout.write(`[${stamp}] [${stage}] ${message}${payload}\n`);
}

function fail(stage, message, extra = {}) {
  log(stage, message, extra);
  process.exitCode = 1;
}

function psql(sql, db = DB_NAME) {
  // 通过 docker exec 跑 psql，避免依赖 npm 包
  const r = spawnSync(
    "docker",
    [
      "exec",
      "-e",
      `PGPASSWORD=${DB_PASS}`,
      PG_CONTAINER,
      "psql",
      "-U", DB_USER,
      "-d", db,
      "-v", "ON_ERROR_STOP=1",
      "-X",
      "-t",
      "-A",
      "-c", sql,
    ],
    { encoding: "utf8" },
  );
  if (r.status !== 0) {
    throw new Error(`psql failed (${r.status}): ${r.stderr || r.stdout}`);
  }
  return r.stdout.trim();
}

function psqlJson(sql, db = DB_NAME) {
  const text = psql(sql, db);
  if (!text) return null;
  try { return JSON.parse(text); } catch { return text; }
}

async function main() {
  // 1. 加载 JSON baseline。
  if (!standardPath) {
    fail(
      "load",
      "找不到 seo-standard.json。请用 STANDARD_JSON_PATH=/path/to/seo-standard.json 启动，或把文件放到以下任一位置：",
      {
        tried: [
          join(repoRoot, "workflows", "seo-standard.json"),
          join(repoRoot, "..", "workflows", "seo-standard.json"),
        ],
      },
    );
    return;
  }
  let standard;
  try {
    standard = JSON.parse(readFileSync(standardPath, "utf8"));
  } catch (error) {
    fail("load", `读取 ${standardPath} 失败：${error.message}`);
    return;
  }

  const version = String(standard.version || "").trim();
  if (!version) {
    fail("load", `${standardPath} 缺少 'version' 字段`);
    return;
  }

  const semverRe = /^[0-9]+\.[0-9]+\.[0-9]+([-.+][0-9A-Za-z.-]+)?$/;
  if (!semverRe.test(version)) {
    fail(
      "validate",
      `version "${version}" 不符合 SemVer；rule_sets_version_chk 会拒绝它`,
    );
    return;
  }

  if (typeof standard !== "object" || standard === null || Array.isArray(standard)) {
    fail("validate", "seo-standard.json 顶层必须是 JSON 对象");
    return;
  }

  log("load", "baseline 已加载", {
    path: standardPath,
    name: RULE_FAMILY_NAME,
    version,
    bytes: Buffer.byteLength(JSON.stringify(standard)),
  });

  // 2. 幂等检查。
  const existing = psql(
    `SELECT id || '|' || is_active || '|' || effective_at::text
       FROM seo_agent.rule_sets
      WHERE name = '${RULE_FAMILY_NAME}' AND source = '${RULE_SOURCE}' AND version = '${version}'
      LIMIT 1`,
  );
  if (existing) {
    const [id, isActive, effectiveAt] = existing.split("|");
    log("skip", "baseline 已存在，无需重复灌入；重跑是 no-op。", {
      id, is_active: isActive, effective_at: effectiveAt,
    });
    return;
  }

  // 3. 冲突保护：同 family 是否已有别处激活。
  const otherActive = psql(
    `SELECT id || '|' || version
       FROM seo_agent.rule_sets
      WHERE name = '${RULE_FAMILY_NAME}' AND is_active = true
      LIMIT 1`,
  );
  if (otherActive) {
    const [id, activeVersion] = otherActive.split("|");
    fail("conflict", "该 family 已有别的 rule_set 行处于激活状态；拒绝静默覆盖", {
      active_id: id, active_version: activeVersion,
    });
    return;
  }

  // 4. 写入 baseline + 审计日志（用一个事务）。
  //    payload 用参数化方式（psql -v variable）传入，避免单引号转义灾难。
  const payloadJson = JSON.stringify(standard);
  const addedKeys = Object.keys(standard).sort();
  const notes = `从 workflows/seo-standard.json v${version} 灌入的初始 baseline`;
  const reason = `从 workflows/seo-standard.json v${version} 灌入 baseline`;

  // 把 JSON 写进临时文件，再用 psql \copy / \\\\copy 不如直接用变量。
  // 用 psql -v 参数传递 JSON（注意：用 dollar-quoting 防止引号被解释）
  const sql = `
    BEGIN;

    WITH ins AS (
      INSERT INTO seo_agent.rule_sets
        (name, version, source, payload, is_active, effective_at, created_by, notes)
      VALUES
        ('${RULE_FAMILY_NAME}',
         '${version}',
         '${RULE_SOURCE}',
         $__payload__$::jsonb,
         true,
         now(),
         '${ACTOR}',
         $__notes__$)
      RETURNING id, version, effective_at
    )
    SELECT id || '|' || version || '|' || effective_at::text FROM ins;
  `;

  // 用 psql \set 注入参数（更稳）
  const setPayload = `\\set payloadJson '${payloadJson.replace(/'/g, "''")}'\n`;
  // dollar-quoted strings can't contain $$，先用 base64 编码 JSON
  const payloadB64 = Buffer.from(payloadJson, "utf8").toString("base64");
  const notesB64 = Buffer.from(notes, "utf8").toString("base64");
  const reasonB64 = Buffer.from(reason, "utf8").toString("base64");
  const addedKeysJson = JSON.stringify(addedKeys);
  const addedKeysB64 = Buffer.from(addedKeysJson, "utf8").toString("base64");

  const fullScript = `
    SELECT pg_backend_pid() AS pid \\gset
    \\set payloadB64 '${payloadB64}'
    \\set notesB64    '${notesB64}'
    \\set reasonB64   '${reasonB64}'
    \\set addedKeysB64 '${addedKeysB64}'

    BEGIN;

    WITH ins AS (
      INSERT INTO seo_agent.rule_sets
        (name, version, source, payload, is_active, effective_at, created_by, notes)
      VALUES
        ('${RULE_FAMILY_NAME}',
         '${version}',
         '${RULE_SOURCE}',
         convert_from(decode(:'payloadB64', 'base64'), 'utf8')::jsonb,
         true,
         now(),
         '${ACTOR}',
         convert_from(decode(:'notesB64', 'base64'), 'utf8'))
      RETURNING id, version, effective_at
    )
    SELECT id, version, effective_at FROM ins \\gset ins_

    INSERT INTO seo_agent.rule_audit_log
      (rule_set_id, action, actor, reason, snapshot_diff)
    VALUES
      (:'ins_id'::bigint,
       'create',
       '${ACTOR}',
       convert_from(decode(:'reasonB64', 'base64'), 'utf8'),
       jsonb_build_object(
         'added_keys',
         convert_from(decode(:'addedKeysB64', 'base64'), 'utf8')::jsonb,
         'source_file', 'workflows/seo-standard.json'
       ));

    INSERT INTO seo_agent.rule_audit_log
      (rule_set_id, action, actor, reason, snapshot_diff)
    VALUES
      (:'ins_id'::bigint,
       'activate',
       '${ACTOR}',
       '首次灌入时自动激活 baseline。',
       '{}'::jsonb);

    COMMIT;

    SELECT :'ins_id' || '|' || :'ins_version' || '|' || :'ins_effective_at' AS final_info;
  `;

  // 把脚本写进临时文件再 psql -f
  const tmpScript = join(repoRoot, "logs", "seed-tmp.sql");
  const { mkdirSync } = await import("node:fs");
  mkdirSync(join(repoRoot, "logs"), { recursive: true });
  const { writeFileSync } = await import("node:fs");
  writeFileSync(tmpScript, fullScript, "utf8");

  // 把脚本拷进容器再 psql -f（这样可以保留 \\set 等元命令）
  const { spawnSync: sp } = await import("node:child_process");
  const cp = sp("docker", ["cp", tmpScript, `${PG_CONTAINER}:/tmp/seed.sql`], { encoding: "utf8" });
  if (cp.status !== 0) {
    fail("insert", `docker cp 失败：${cp.stderr}`, { code: cp.status });
    return;
  }

  let out;
  try {
    out = psql("\\i /tmp/seed.sql");
  } catch (error) {
    fail("insert", `事务失败：${error.message}`);
    return;
  }

  const lastLine = out.split("\n").filter(Boolean).pop() || "";
  const [id, v, effectiveAt] = lastLine.split("|");
  log("insert", "rule_sets 行已创建", {
    id, version: v, effective_at: effectiveAt,
  });
  log("done", "baseline 灌入成功；v_active_rule_set 已生效", { id, version: v });
}

main().catch((error) => {
  fail("fatal", error.message, { stack: error.stack });
});
