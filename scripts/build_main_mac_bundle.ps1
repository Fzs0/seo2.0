param(
    [string]$OutputRoot = "outputs\mac-migration"
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$bundleName = "seo2-main-mac-$stamp"
$outputRootPath = Join-Path $projectRoot $OutputRoot
$bundleRoot = Join-Path $outputRootPath $bundleName
$zipPath = "$bundleRoot.zip"

if (-not $bundleRoot.StartsWith((Join-Path $projectRoot "outputs"), [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Output must stay below the project outputs directory."
}

New-Item -ItemType Directory -Force -Path $bundleRoot | Out-Null

$rootFiles = @(
    ".env.example",
    ".gitignore",
    "alembic.ini",
    "docker-compose.yml",
    "pytest.ini",
    "requirements.txt",
    "README.md",
    "PROJECT_PROGRESS.md"
)

$directories = @(
    "app",
    "frontend",
    "tests",
    "db",
    "alembic",
    "scripts",
    "docs",
    "workflows"
)

foreach ($file in $rootFiles) {
    $source = Join-Path $projectRoot $file
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $bundleRoot $file) -Force
    }
}

foreach ($directory in $directories) {
    $source = Join-Path $projectRoot $directory
    if (Test-Path -LiteralPath $source) {
        $destination = Join-Path $bundleRoot $directory
        New-Item -ItemType Directory -Force -Path $destination | Out-Null
        & robocopy $source $destination /E /NFL /NDL /NJH /NJS /NC /NS /NP `
            /XD node_modules dist __pycache__ .pytest_cache .codegraph `
            /XF *.pyc *.pyo *.log .DS_Store | Out-Null
        if ($LASTEXITCODE -ge 8) {
            throw "robocopy failed for $directory with exit code $LASTEXITCODE"
        }
    }
}

$removeRelativePaths = @(
    "frontend\node_modules",
    "frontend\dist",
    "frontend\.codegraph",
    "frontend\src\data\social.ts",
    "frontend\src\data\socialPresentation.ts",
    "frontend\src\pages\SocialPublishingPage.tsx",
    "frontend\tests\socialPresentation.test.ts",
    "app\api\v1\social.py",
    "app\clients\social_executor.py",
    "app\connectors\social_connections.py",
    "app\services\social_connection_service.py",
    "app\services\social_delivery_service.py",
    "app\services\social_extension_service.py",
    "app\services\social_platform_registry.py",
    "app\services\social_publishing_service.py",
    "tests\test_social_connections.py",
    "tests\test_social_delivery_batch.py",
    "tests\test_social_extension_contract.py",
    "tests\test_social_publishing_api.py",
    "tests\test_social_schema_migration.py",
    "scripts\extension_tunnel_relay.py",
    "scripts\prepare_social_batch.py",
    "scripts\start_extension_tunnel.py",
    "scripts\start_social_executor.py",
    "scripts\export_knowledge_claims.py",
    "scripts\migrate_knowledge_claims.py",
    "scripts\node_modules",
    "db\migrations\022_social_publishing_foundation.sql",
    "db\migrations\023_social_connections.sql",
    "db\migrations\024_social_executor_delivery.sql",
    "db\migrations\025_social_schema.sql",
    "db\migrations\027_social_extension_pairing.sql",
    "db\migrations\028_social_extension_device_uniqueness.sql",
    "docs\KNOWLEDGE_BRAIN_INTEGRATION.md"
)

foreach ($relativePath in $removeRelativePaths) {
    $target = Join-Path $bundleRoot $relativePath
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}

Get-ChildItem -LiteralPath $bundleRoot -Recurse -Directory -Force |
    Where-Object { $_.Name -in @("__pycache__", ".pytest_cache", "dist", "node_modules") } |
    Sort-Object FullName -Descending |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }

Get-ChildItem -LiteralPath $bundleRoot -Recurse -File -Force |
    Where-Object { $_.Extension -in @(".pyc", ".pyo", ".log") -or $_.Name -eq ".DS_Store" } |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }

$requirementsPath = Join-Path $bundleRoot "requirements.txt"
$requirements = Get-Content -LiteralPath $requirementsPath -Raw
$requirements = $requirements -replace "(?ms)\r?\n# Canonical social platform contract shared with the standalone publisher\.\r?\n-e \./social-publisher/shared/python\r?\n?", "`r`n"
Set-Content -LiteralPath $requirementsPath -Value $requirements -Encoding utf8

$pytestPath = Join-Path $bundleRoot "pytest.ini"
$pytestConfig = Get-Content -LiteralPath $pytestPath -Raw
$pytestConfig = $pytestConfig -replace "(?m)^\s+social-publisher/backend/tests\r?\n", ""
$pytestConfig = $pytestConfig -replace "(?m)^\s+social-publisher/shared/python/tests\r?\n", ""
Set-Content -LiteralPath $pytestPath -Value $pytestConfig -Encoding utf8

$mainPath = Join-Path $bundleRoot "app\main.py"
$mainSource = Get-Content -LiteralPath $mainPath -Raw
$mainSource = $mainSource -replace "(?m)^\s+from app\.api\.v1\.social import router as v1_social_router\r?\n", ""
$mainSource = $mainSource -replace "(?m)^\s+app\.include_router\(v1_social_router, prefix=""/api/v1""\)\r?\n", ""
Set-Content -LiteralPath $mainPath -Value $mainSource -Encoding utf8

$appPath = Join-Path $bundleRoot "frontend\src\App.tsx"
$appSource = Get-Content -LiteralPath $appPath -Raw
$appSource = $appSource -replace "(?m)^import \{ SocialPublishingPage \} from '@/pages/SocialPublishingPage'\r?\n", ""
$appSource = $appSource -replace "(?m)^\s+\| 'social-publishing'\r?\n", ""
$appSource = $appSource -replace "(?ms)\s+case 'social-publishing':\r?\n\s+return <SocialPublishingPage />", ""
Set-Content -LiteralPath $appPath -Value $appSource -Encoding utf8

$sidebarPath = Join-Path $bundleRoot "frontend\src\components\Sidebar.tsx"
$sidebarSource = Get-Content -LiteralPath $sidebarPath -Raw
$sidebarSource = $sidebarSource -replace "(?m)^\s+\{ id: 'social-publishing', label: '社媒发布', icon: 'campaign' \},\r?\n", ""
Set-Content -LiteralPath $sidebarPath -Value $sidebarSource -Encoding utf8

$envExamplePath = Join-Path $bundleRoot ".env.example"
$envExample = Get-Content -LiteralPath $envExamplePath -Raw
$envExample = $envExample -replace "(?ms)# Local Hubstudio/Puppeteer executor\..*?SOCIAL_EXECUTOR_SHARED_SECRET=\r?\n", ""
Set-Content -LiteralPath $envExamplePath -Value $envExample -Encoding utf8

$macScript = @'
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

for command_name in python3 node npm docker; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Missing dependency: $command_name"
    exit 1
  }
done

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
npm --prefix frontend ci

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env. Fill private keys before starting the backend."
fi

docker compose up -d postgres redis
echo
echo "Dependencies are ready."
echo "Initialize or restore PostgreSQL before first application start."
echo "Backend: .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000"
echo "Frontend: npm --prefix frontend run dev -- --host 127.0.0.1"
'@
$macScriptPath = Join-Path $bundleRoot "scripts\setup-macos-main.sh"
Set-Content -LiteralPath $macScriptPath -Value $macScript -Encoding utf8NoBOM

$guide = @'
# SEO Workbench 主项目迁移到 Mac

本包只包含 SEO Workbench 主项目。

已排除：

- 独立 `knowledge/` 服务；
- `social-publisher/`、`social-executor/`；
- Hubstudio 扩展和 Native Host；
- 主项目中的社媒 API、页面、测试和社媒数据库迁移；
- `node_modules`、虚拟环境、构建结果、日志、输出、缓存；
- `.env`、`config/*.local.json`、数据库备份等敏感或本机数据。

注意：主项目内部的 `knowledge_profile`、站点知识画像和
`seo_agent.knowledge_claims` 仍然保留。它们属于 SEO 策略数据结构，
不是已排除的独立 Knowledge 服务。

## 1. Mac 依赖

建议安装：

- macOS 13+；
- Homebrew；
- Python 3.11 或 3.12；
- Node.js 20+；
- Docker Desktop；
- Git（可选）。

```bash
brew install python@3.12 node
```

安装并启动 Docker Desktop 后，在项目根目录执行：

```bash
chmod +x scripts/setup-macos-main.sh
./scripts/setup-macos-main.sh
```

## 2. 私密配置

本包故意不包含真实密钥。请从旧电脑通过 AirDrop、加密磁盘映像或密码管理器
单独迁移以下文件，不要通过聊天、Git 或公开网盘传输：

```text
.env
config/ai-stages.local.json
config/blog-sites.local.json
config/google-data-sources.local.json
config/image-providers.local.json
config/main-sites.local.json
config/product-assets.local.json
config/serpapi.local.json
config/wp-sites.local.json
```

如果迁移现有加密 Token，必须同时迁移原 `CONNECTOR_SECRET_KEY`；
否则数据库中的 OEMApps、Shopify 等加密凭据无法解密。

首次启动前保持：

```dotenv
AUTOMATION_ENABLED=false
```

## 3. 数据库

仅复制项目源码不会带走 Docker volume。若要保留当前站点、商品、关键词、
GSC/GA4、文章和策略历史，需要在旧电脑生成最新 PostgreSQL custom dump，
再单独安全传输。

旧电脑：

```powershell
docker exec pg-workbench pg_dump -U seo -d seo_workbench -Fc -f /tmp/seo_workbench.dump
docker cp pg-workbench:/tmp/seo_workbench.dump .\migration-data\seo_workbench_latest.dump
```

Mac：

```bash
docker compose up -d postgres redis
docker cp seo_workbench_latest.dump pg-workbench:/tmp/seo_workbench.dump
docker exec pg-workbench pg_restore \
  -U seo -d seo_workbench --clean --if-exists --no-owner \
  /tmp/seo_workbench.dump
```

数据库备份含真实业务数据，应和密钥文件一样单独保护。

如果不迁移历史数据，可用 `scripts/start.sh` 初始化空数据库。

## 4. 启动

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
npm --prefix frontend run dev -- --host 127.0.0.1
```

打开：

- 前端：http://127.0.0.1:5173
- API 文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/health

## 5. 验收顺序

1. PostgreSQL 17 和 Redis healthy；
2. `/api/health` 返回成功；
3. 前端可进入站点、关键词、文章、主站内容、数据复盘；
4. 核对站点、商品、文章、关键词、GSC/GA4 数量；
5. 测试 Google 数据源和站点连接器的只读接口；
6. 确认 `AUTOMATION_ENABLED=false`；
7. 先做一条低风险预览和人工审批，不直接批量写远端。

## 6. 本包边界

该副本没有社媒发布菜单和 `/api/v1/social/*` API。旧数据库中即使存在
`social` schema，也不会被本包调用。若未来需要社媒功能，应迁移完整仓库，
不要把本包当作社媒系统恢复源。
'@
Set-Content -LiteralPath (Join-Path $bundleRoot "MAC迁移说明.md") -Value $guide -Encoding utf8

$excludedTopLevel = @(
    "knowledge",
    "social-publisher",
    "social-executor",
    "hubstudio-social-extension",
    "hubstudio-native-host",
    "logs",
    "outputs",
    "migration-data"
)
foreach ($name in $excludedTopLevel) {
    if (Test-Path -LiteralPath (Join-Path $bundleRoot $name)) {
        throw "Forbidden directory remained in bundle: $name"
    }
}

$forbiddenSecretFiles = Get-ChildItem -LiteralPath $bundleRoot -Recurse -File -Force |
    Where-Object {
        $_.Name -eq ".env" -or
        $_.Name -like "*.local.json" -or
        $_.Extension -in @(".pem", ".key", ".dump")
    }
if ($forbiddenSecretFiles) {
    throw "Sensitive files remained in bundle: $($forbiddenSecretFiles.FullName -join ', ')"
}

$manifestRows = Get-ChildItem -LiteralPath $bundleRoot -Recurse -File |
    Sort-Object FullName |
    ForEach-Object {
        [pscustomobject]@{
            path = $_.FullName.Substring($bundleRoot.Length + 1).Replace("\", "/")
            bytes = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
$manifestRows | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $bundleRoot "MANIFEST.sha256.json") -Encoding utf8

if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -LiteralPath $bundleRoot -DestinationPath $zipPath -CompressionLevel Optimal

$bundleFiles = @(Get-ChildItem -LiteralPath $bundleRoot -Recurse -File)
$bundleBytes = ($bundleFiles | Measure-Object Length -Sum).Sum

[pscustomobject]@{
    BundleDirectory = $bundleRoot
    Zip = $zipPath
    FileCount = $bundleFiles.Count
    UncompressedMB = [math]::Round($bundleBytes / 1MB, 2)
    ZipMB = [math]::Round((Get-Item -LiteralPath $zipPath).Length / 1MB, 2)
    ZipSHA256 = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
} | Format-List
