#!/usr/bin/env node
// =====================================================================
// 数据迁移脚本：把"错位存储在 posts 表里的产品"挪到正经的 products 表。
//
// 步骤：
//   1. 读 config/product-assets.local.json 的 products[]。
//   2. 在容器内 psql 删 posts.source='product-assets.local.json' 的所有行
//      （错位数据，幂等可重跑）。
//   3. 把 products[] 逐条 INSERT 到 seo_agent.products，site_id 用 exdivo。
//      ON CONFLICT (external_id) DO UPDATE，幂等。
//
// 用法（在 seo-2.0/ 目录下）：
//   node scripts/migrate_products_to_v2.mjs
//
// 依赖：004_products.sql 已执行；config/*.local.json 已就位。
// =====================================================================

import { dirname, join, resolve } from "node:path";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const PG_CONTAINER = process.env.PG_CONTAINER || "pg-workbench";
const DB_USER = process.env.DB_USER || "seo";
const DB_PASS = process.env.DB_PASS || "seo_dev_local";
const DB_NAME = process.env.DB_NAME || "seo_workbench";

// 用 process.argv[1] 而不是 import.meta.url。
// 在 Windows + Git Bash 下，import.meta.url 经过 fileURLToPath + dirname 后
// 会少一层（实测 here=C:\Users\PC\Desktop\seo-2.0 而不是 scripts\）。
const scriptPath = process.argv[1] || fileURLToPath(import.meta.url);
const here = dirname(scriptPath);
const repoRoot = resolve(here, "..");
const productsJson = join(repoRoot, "config", "product-assets.local.json");

function log(stage, msg, extra = {}) {
  const stamp = new Date().toISOString();
  const payload = Object.keys(extra).length ? ` ${JSON.stringify(extra)}` : "";
  process.stdout.write(`[${stamp}] [${stage}] ${msg}${payload}\n`);
}

function fail(stage, msg, extra = {}) {
  log(stage, msg, extra);
  process.exitCode = 1;
}

// 通过 docker exec + stdin 把 SQL 喂给 psql，避免命令行引号转义陷阱
// 注意：Node ESM 下 spawnSync 第一个参数必须是 string，不能传数组
function psql(sql) {
  const r = spawnSync(
    "docker",
    [
      "exec", "-i", "-e", `PGPASSWORD=${DB_PASS}`, PG_CONTAINER,
      "psql", "-U", DB_USER, "-d", DB_NAME, "-v", "ON_ERROR_STOP=1",
      "-t", "-A", "-X",
    ],
    { input: sql, encoding: "utf-8" }
  );
  if (r.status !== 0) {
    throw new Error(`psql failed (exit=${r.status}):\nSTDERR: ${r.stderr}\nSTDOUT: ${r.stdout}\nSQL: ${sql.slice(0, 200)}...`);
  }
  return r.stdout.trim();
}

function esc(s) { return String(s).replace(/'/g, "''"); }

async function main() {
  if (!existsSync(productsJson)) {
    fail("load", `找不到 ${productsJson}`, {
      here, repoRoot,
      hint: "在 seo-2.0/ 目录下运行，或设置 STANDARD_JSON_PATH 环境变量",
    });
    return;
  }
  const cfg = JSON.parse(readFileSync(productsJson, "utf-8"));
  const products = cfg.products || [];
  log("load", `读入 ${products.length} 条产品`, { path: productsJson });

  // 0. 查找 exdivo 主站 UUID
  const siteIdRow = psql(
    `SELECT id FROM seo_agent.sites WHERE site_key = 'exdivo' LIMIT 1`
  );
  if (!siteIdRow) {
    fail("lookup", "找不到 site_key='exdivo' 的主站。请先跑 sync_sites_and_products.py");
    return;
  }
  const siteId = siteIdRow;
  log("lookup", `主站 exdivo → ${siteId}`);

  // 1. 删除错位数据（幂等：删 0 行也无副作用）
  const beforePostsCount = psql(
    `SELECT count(*) FROM seo_agent.posts WHERE source = 'product-assets.local.json'`
  );
  log("posts", `删除前 posts 表里的产品错位数据：${beforePostsCount} 行`);
  if (Number(beforePostsCount) > 0) {
    psql(`DELETE FROM seo_agent.posts WHERE source = 'product-assets.local.json'`);
    const afterPostsCount = psql(
      `SELECT count(*) FROM seo_agent.posts WHERE source = 'product-assets.local.json'`
    );
    log("posts", `删除后 posts 表里的产品错位数据：${afterPostsCount} 行`);
  }

  // 2. 逐条 INSERT products
  let inserted = 0, updated = 0;
  for (const p of products) {
    const externalId = String(p.id || "").trim();
    if (!externalId) continue;
    const title = p.title || "";
    if (!title) continue;
    const handle = esc(p.handle || "");
    const url = esc(p.url || "");
    const category = esc(p.category || "");
    const description = esc(p.description || "");
    const status = String(p.status) === "1" ? "active" : "draft";
    const rawJson = JSON.stringify(p);
    const imageJson = "{}";
    const keywordsArr = Array.isArray(p.keywords) ? p.keywords : [];
    const keywordsLit = keywordsArr.map(k => `'${esc(String(k))}'`).join(",");
    const titleEsc = esc(title);

    const sql = `
      INSERT INTO seo_agent.products
        (external_id, site_id, handle, title, url, category, price,
         status, image, description, keywords, source, raw)
      VALUES
        ('${esc(externalId)}',
         '${esc(siteId)}'::uuid,
         '${handle}',
         '${titleEsc}',
         '${url}',
         '${category}',
         NULL,
         '${status}',
         '${imageJson}'::jsonb,
         '${description}',
         ARRAY[${keywordsLit}]::text[],
         'product-assets.local.json',
         '${rawJson.replace(/'/g, "''")}'::jsonb)
      ON CONFLICT (external_id) DO UPDATE SET
        site_id      = EXCLUDED.site_id,
        handle       = EXCLUDED.handle,
        title        = EXCLUDED.title,
        url          = EXCLUDED.url,
        category     = EXCLUDED.category,
        status       = EXCLUDED.status,
        image        = EXCLUDED.image,
        description  = EXCLUDED.description,
        keywords     = EXCLUDED.keywords,
        source       = EXCLUDED.source,
        raw          = EXCLUDED.raw,
        updated_at   = now()
      RETURNING (xmax = 0) AS was_insert;
    `.trim();
    const ret = psql(sql);
    if (ret === "t") inserted += 1; else updated += 1;
  }
  log("insert", `写入完成：${inserted} 新增，${updated} 更新`);

  // 3. 验证
  const counts = psql(`
    SELECT
      (SELECT count(*) FROM seo_agent.products) AS products,
      (SELECT count(*) FROM seo_agent.products WHERE status='active') AS active,
      (SELECT count(*) FROM seo_agent.products WHERE status='draft') AS draft,
      (SELECT count(*) FROM seo_agent.posts WHERE source='product-assets.local.json') AS posts_left
  `);
  log("verify", `products 表：${counts}`);
}

main().catch((error) => {
  fail("fatal", error.message, { stack: error.stack });
});