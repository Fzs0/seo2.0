"""把 config/*.local.json 里的站点 + 产品同步到 seo_agent 数据库。

读：
  - config/main-sites.local.json (主站 → sites 表)
  - config/blog-sites.local.json (博客站 → sites 表，site_type=blog)
  - config/wp-sites.local.json   (WordPress 站 → sites 表，site_type=wp)
  - config/product-assets.local.json (产品 → products 表，**不是 posts**)

写：
  - seo_agent.sites (ON CONFLICT site_key DO UPDATE)
  - seo_agent.products (ON CONFLICT external_id DO UPDATE；不写 posts)

约束：
  - 不引第三方依赖（用 docker exec 跑 psql）
  - 把 JSON 字段展平为 SQL 列
  - 写入幂等：重复执行覆盖，不新增重复行
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
PG_CONTAINER = "pg-workbench"
DB_USER = "seo"
DB_PASS = "seo_dev_local"
DB_NAME = "seo_workbench"


def psql(sql: str) -> str:
    """通过 docker exec + stdin 把 SQL 喂给 psql，避免命令行引号转义陷阱。

    注意：subprocess.run 第 1 个位置参数是 program 名（必须 string），其余参数走列表。
    """
    r = subprocess.run(
        [
            "docker", "exec", "-i", "-e", f"PGPASSWORD={DB_PASS}", PG_CONTAINER,
            "psql", "-U", DB_USER, "-d", DB_NAME, "-v", "ON_ERROR_STOP=1",
            "-t", "-A", "-X",
        ],
        input=sql,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"psql failed (exit={r.returncode}):\nSTDERR: {r.stderr}\nSTDOUT: {r.stdout}\nSQL: {sql[:200]}..."
        )
    return r.stdout.strip()


def esc(value: str) -> str:
    """SQL 字符串字面量转义（单引号）。"""
    return str(value).replace("'", "''")


def upsert_sites() -> dict[str, str]:
    """写 sites 表，返回 {site_key: id(uuid字符串)}。"""
    site_key_to_id: dict[str, str] = {}

    # 主站
    main = json.loads((CONFIG / "main-sites.local.json").read_text(encoding="utf-8"))
    for s in main.get("sites", []):
        key = s["name"]
        sql = f"""
        INSERT INTO seo_agent.sites (site_key, name, site_type, domain, base_url, api_base_url, content_role, status, raw, api_config)
        VALUES (
          '{esc(key)}',
          '{esc(s.get("name", key))}',
          'main',
          '{esc(s.get("apiBaseUrl", ""))}',
          '{esc(s.get("apiBaseUrl", ""))}',
          '{esc(s.get("apiBaseUrl", ""))}',
          '{esc(s.get("contentRole", ""))}',
          'active',
          '{esc(json.dumps(s, ensure_ascii=False))}'::jsonb,
          jsonb_strip_nulls(jsonb_build_object(
            'tokenA', NULLIF('{esc(s.get("tokenA", ""))}', ''),
            'tokenB', NULLIF('{esc(s.get("tokenB", ""))}', ''),
            'articleUrlPath', NULLIF('{esc(s.get("articleUrlPath", ""))}', '')
          ))
        )
        ON CONFLICT (site_key) DO UPDATE SET
          name = EXCLUDED.name,
          api_config = seo_agent.sites.api_config || EXCLUDED.api_config,
          content_role = EXCLUDED.content_role,
          raw = EXCLUDED.raw,
          updated_at = now();
        """
        psql(sql)
        row = psql(f"SELECT id FROM seo_agent.sites WHERE site_key = '{esc(key)}'")
        site_key_to_id[key] = row
        print(f"  + main site: {key} -> {row}")

    # 博客站
    blog = json.loads((CONFIG / "blog-sites.local.json").read_text(encoding="utf-8"))
    for s in blog.get("sites", []):
        key = s["name"]
        sql = f"""
        INSERT INTO seo_agent.sites (site_key, name, site_type, domain, base_url, api_base_url, content_role, status, raw, api_config)
        VALUES (
          '{esc(key)}',
          '{esc(s.get("name", key))}',
          'blog',
          '{esc(s.get("apiBaseUrl", ""))}',
          '{esc(s.get("apiBaseUrl", ""))}',
          '{esc(s.get("apiBaseUrl", ""))}',
          '{esc(s.get("contentRole", ""))}',
          'active',
          '{esc(json.dumps(s, ensure_ascii=False))}'::jsonb,
          jsonb_strip_nulls(jsonb_build_object(
            'openApiKey', NULLIF('{esc(s.get("openApiKey", ""))}', ''),
            'defaultAuthor', NULLIF('{esc(s.get("defaultAuthor", ""))}', ''),
            'connector_type', 'custom_openapi',
            'articlesPath', '/posts',
            'publishPath', '/posts',
            'articleUrlPath', NULLIF('{esc(s.get("articleUrlPath", ""))}', '')
          ))
        )
        ON CONFLICT (site_key) DO UPDATE SET
          name = EXCLUDED.name,
          api_config = seo_agent.sites.api_config || EXCLUDED.api_config,
          content_role = EXCLUDED.content_role,
          raw = EXCLUDED.raw,
          updated_at = now();
        """
        psql(sql)
        row = psql(f"SELECT id FROM seo_agent.sites WHERE site_key = '{esc(key)}'")
        site_key_to_id[key] = row
        print(f"  + blog site: {key} -> {row}")

    # WordPress 站
    wp = json.loads((CONFIG / "wp-sites.local.json").read_text(encoding="utf-8"))
    for s in wp.get("sites", []):
        key = s["name"]
        sql = f"""
        INSERT INTO seo_agent.sites (site_key, name, site_type, domain, base_url, content_role, status, raw, api_config)
        VALUES (
          '{esc(key)}',
          '{esc(s.get("name", key))}',
          'wp',
          '{esc(s.get("siteUrl", ""))}',
          '{esc(s.get("siteUrl", ""))}',
          '{esc(s.get("contentRole", ""))}',
          'active',
          '{esc(json.dumps(s, ensure_ascii=False))}'::jsonb,
          jsonb_build_object('username', '{esc(s.get("username", ""))}', 'applicationPassword', '{esc(s.get("applicationPassword", ""))}')
        )
        ON CONFLICT (site_key) DO UPDATE SET
          name = EXCLUDED.name,
          api_config = EXCLUDED.api_config,
          content_role = EXCLUDED.content_role,
          raw = EXCLUDED.raw,
          updated_at = now();
        """
        psql(sql)
        row = psql(f"SELECT id FROM seo_agent.sites WHERE site_key = '{esc(key)}'")
        site_key_to_id[key] = row
        print(f"  + wp site: {key} -> {row}")

    return site_key_to_id


def upsert_products(site_key_to_id: dict[str, str]) -> int:
    """写产品到 **products** 表（products 表在 migration 004 里）。"""
    pa = json.loads((CONFIG / "product-assets.local.json").read_text(encoding="utf-8"))
    products = pa.get("products", [])

    # 选一个主站作为 site_id（优先 exdivo）
    primary_site_key = "exdivo" if "exdivo" in site_key_to_id else next(iter(site_key_to_id))
    site_id = site_key_to_id[primary_site_key]

    n = 0
    for p in products:
        pid = str(p.get("id", "")).strip()
        if not pid:
            continue
        title = p.get("title") or ""
        handle = p.get("handle") or ""
        url = p.get("url") or ""
        description = p.get("description") or ""
        category = p.get("category") or ""
        # 状态映射：1 → active，0 → draft
        status = "active" if str(p.get("status")) == "1" else "draft"
        keywords_arr = p.get("keywords") or []
        if not isinstance(keywords_arr, list):
            keywords_arr = []
        # ARRAY['a','b'] 字面量
        keywords_literal = (
            "[" + ",".join(f'"{esc(str(k))}"' for k in keywords_arr) + "]"
        )
        # image 字段：源 openapi 返回字符串 "[object Object]"，统一存空对象
        image_literal = "'{}'::jsonb"

        sql = f"""
        INSERT INTO seo_agent.products
          (external_id, site_id, handle, title, url, category, price,
           status, image, description, keywords, source, raw)
        VALUES (
          '{esc(pid)}',
          '{esc(site_id)}'::uuid,
          '{esc(handle)}',
          '{esc(title)}',
          '{esc(url)}',
          '{esc(category)}',
          NULL,
          '{esc(status)}',
          {image_literal},
          '{esc(description)}',
          ARRAY{keywords_literal}::text[],
          'product-assets.local.json',
          '{esc(json.dumps(p, ensure_ascii=False))}'::jsonb
        )
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
          updated_at   = now();
        """
        psql(sql)
        n += 1
    print(f"  + products: {n} (site={primary_site_key})")
    return n


def main() -> None:
    print("[1/2] 写站点 → seo_agent.sites")
    site_ids = upsert_sites()
    print(f"  共 {len(site_ids)} 个站点")
    print("[2/2] 写产品 → seo_agent.products")
    n = upsert_products(site_ids)
    print(f"  共 {n} 个产品")


if __name__ == "__main__":
    main()
