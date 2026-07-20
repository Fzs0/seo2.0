#!/usr/bin/env bash
# =====================================================================
# seo-2.0 一键启动（Windows + Git Bash 友好）
#
# 步骤：
#   1. 起 PG + Redis 容器（如果还没起）
#   2. 按编号跑全部数据库 migration
#   3. 灌 baseline（幂等，重跑是 no-op）
#   4. 复制 .env.example → .env（如不存在）
#   5. 提示用户 pip install + uvicorn（脚本不替你装）
#
# 用法（在 seo-2.0/ 目录下）：
#   bash scripts/start.sh
#
# 跳步骤：
#   SKIP_DOCKER=1    跳过 docker compose（容器已起）
#   SKIP_MIGRATE=1   跳过 migration（库已建好）
#   SKIP_SEED=1      跳过 seed baseline
#
# 启动日志：
#   logs/start.log（自动创建目录）。每次启动追加；屏幕与文件双写。
#   清空旧日志：rm logs/start.log
#   DRY_RUN=1       只写日志不执行副作用（与 SKIP_DOCKER/SKIP_MIGRATE/SKIP_SEED 不冲突）
# =====================================================================

set -uo pipefail

# ---------- 路径与变量 ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT}"

LOG_DIR="${ROOT}/logs"
LOG_FILE="${LOG_DIR}/start.log"
mkdir -p "${LOG_DIR}"

PG_CONTAINER="pg-workbench"
REDIS_CONTAINER="redis-workbench"
PG_PORT="${PG_PORT:-5433}"
DB_USER="${DB_USER:-seo}"
DB_PASS="${DB_PASS:-seo_dev_local}"
DB_NAME="${DB_NAME:-seo_workbench}"
DATABASE_URL="${DATABASE_URL:-postgresql://${DB_USER}:${DB_PASS}@localhost:${PG_PORT}/${DB_NAME}}"
DRY_RUN="${DRY_RUN:-0}"

# ---------- 颜色（仅 TTY 有效；写文件时无 ANSI） ----------
if [[ -t 1 ]]; then
  C_OK="\033[1;32m"; C_WARN="\033[1;33m"; C_FAIL="\033[1;31m"; C_INFO="\033[1;36m"; C_OFF="\033[0m"
else
  C_OK=""; C_WARN=""; C_FAIL=""; C_INFO=""; C_OFF=""
fi

# ---------- 日志输出：同时到 stderr 和文件 ----------
ts() { date '+%Y-%m-%d %H:%M:%S.%3N'; }

# 主日志函数。所有屏幕输出走它。$1=level $2=step $3=msg
log_line() {
  local level="$1" step="$2" msg="$3"
  local line
  line="[$(ts)] [${level}] [${step}] ${msg}"
  # 文件（无颜色）
  printf '%s\n' "${line}" >> "${LOG_FILE}"
  # 屏幕（带颜色）
  local color=""
  case "${level}" in
    OK)   color="${C_OK}"   ;;
    WARN) color="${C_WARN}" ;;
    FAIL) color="${C_FAIL}" ;;
    START)color="${C_INFO}" ;;
  esac
  printf '%s%s%s\n' "${color}" "${line}" "${C_OFF}" >&2
}

log_ok()   { log_line OK    "$1" "$2"; }
log_warn() { log_line WARN  "$1" "$2"; }
log_fail() { log_line FAIL  "$1" "$2"; }
log_info() { log_line START "$1" "$2"; }

# 跑任意命令并捕获退出码；DRY_RUN=1 时只打印不执行
# 用法：run_cmd step message cmd args...
run_cmd() {
  local step="$1" msg="$2"; shift 2
  log_info "${step}" "${msg}"
  if [[ "${DRY_RUN}" == "1" ]]; then
    log_info "${step}" "DRY_RUN=1，不执行：$*"
    return 0
  fi
  if "$@"; then
    log_ok "${step}" "${msg} ✓"
    return 0
  else
    local rc=$?
    log_fail "${step}" "${msg} ✗（退出码 ${rc}）"
    return ${rc}
  fi
}

# ---------- 启动横幅 ----------
{
  printf '%s\n' "==============================================================="
  printf '%s\n' "seo-2.0 start.sh @ $(ts)"
  printf '%s\n' "ROOT=${ROOT}"
  printf '%s\n' "DATABASE_URL=${DATABASE_URL}"
  printf '%s\n' "DRY_RUN=${DRY_RUN}  SKIP_DOCKER=${SKIP_DOCKER:-0}  SKIP_MIGRATE=${SKIP_MIGRATE:-0}  SKIP_SEED=${SKIP_SEED:-0}"
  printf '%s\n' "==============================================================="
} >> "${LOG_FILE}"
log_info "init" "启动开始；日志写入 ${LOG_FILE}"

# ---------- 1. docker compose ----------
if [[ "${SKIP_DOCKER:-0}" != "1" ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    log_fail "docker" "docker 未安装"
    exit 1
  fi
  log_info "docker" "检查容器状态..."

  container_running() {
    docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$1"
  }
  container_exists() {
    docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$1"
  }

  for c in "${PG_CONTAINER}" "${REDIS_CONTAINER}"; do
    if container_running "${c}"; then
      log_ok "docker" "容器 ${c} 已在运行，跳过"
    elif container_exists "${c}"; then
      log_warn "docker" "容器 ${c} 存在但未运行，docker start"
      run_cmd "docker" "start ${c}" docker start "${c}" || exit $?
    else
      log_warn "docker" "容器 ${c} 不存在，docker compose up"
      run_cmd "docker" "compose up -d postgres redis" \
        docker compose up -d postgres redis || exit $?
      break
    fi
  done
else
  log_warn "docker" "SKIP_DOCKER=1，跳过"
fi

# 等 PG ready
log_info "wait-pg" "等待 PG 在 ${PG_PORT} 可连..."
ready=0
for i in {1..30}; do
  if MSYS_NO_PATHCONV=1 docker exec "${PG_CONTAINER}" pg_isready -U "${DB_USER}" -d "${DB_NAME}" >/dev/null 2>&1; then
    log_ok "wait-pg" "PG 已就绪（第 ${i} 次尝试）"
    ready=1
    break
  fi
  sleep 1
done
if [[ "${ready}" != "1" ]]; then
  log_fail "wait-pg" "PG 在 30s 内未就绪"
  exit 1
fi

# ---------- 2. migration ----------
run_psql() {
  local file="$1"
  local step="migrate"
  local basename
  basename="$(basename "${file}")"
  log_info "${step}" "复制 ${file} → ${PG_CONTAINER}:/tmp/${basename}"
  if [[ "${DRY_RUN}" == "1" ]]; then
    log_info "${step}" "DRY_RUN=1，跳过 docker cp 与 psql"
    return 0
  fi
  MSYS_NO_PATHCONV=1 docker cp "${file}" "${PG_CONTAINER}:/tmp/${basename}" || {
    log_fail "${step}" "docker cp 失败：${file}"
    return 1
  }
  log_info "${step}" "执行 psql -f /tmp/${basename}"
  if MSYS_NO_PATHCONV=1 docker exec "${PG_CONTAINER}" psql \
      -U "${DB_USER}" -d "${DB_NAME}" -v ON_ERROR_STOP=1 -f "/tmp/${basename}" \
      >/dev/null 2>>"${LOG_FILE}"; then
    log_ok "${step}" "${basename} 完成"
    return 0
  else
    local rc=$?
    log_fail "${step}" "${basename} 失败（退出码 ${rc}）"
    return ${rc}
  fi
}

if [[ "${SKIP_MIGRATE:-0}" != "1" ]]; then
  for migration in db/migrations/*.sql; do
    run_psql "$migration" || exit $?
  done
else
  log_warn "migrate" "SKIP_MIGRATE=1，跳过"
fi

# ---------- 3. seed ----------
if [[ "${SKIP_SEED:-0}" != "1" ]]; then
  log_info "seed" "灌 baseline（幂等）"
  if [[ "${DRY_RUN}" == "1" ]]; then
    log_info "seed" "DRY_RUN=1，跳过 node 脚本"
  else
    # seed 会按以下顺序找 seo-standard.json：
    #   1. ${STANDARD_JSON_PATH}
    #   2. ${ROOT}/workflows/seo-standard.json
    #   3. ${ROOT}/../workflows/seo-standard.json
    if [[ -n "${STANDARD_JSON_PATH:-}" ]]; then
      log_info "seed" "使用 STANDARD_JSON_PATH=${STANDARD_JSON_PATH}"
    else
      log_warn "seed" "未设置 STANDARD_JSON_PATH；如失败请用 STANDARD_JSON_PATH=/path/to/seo-standard.json 重跑"
    fi
    DATABASE_URL="${DATABASE_URL}" STANDARD_JSON_PATH="${STANDARD_JSON_PATH:-}" \
      node db/scripts/seed_rule_baseline.mjs >>"${LOG_FILE}" 2>&1
    seed_rc=$?
    if [[ "${seed_rc}" == "0" ]]; then
      log_ok "seed" "baseline 灌入成功"
    else
      log_fail "seed" "seed 失败（退出码 ${seed_rc}）；日志见 ${LOG_FILE}"
      exit "${seed_rc}"
    fi
  fi
else
  log_warn "seed" "SKIP_SEED=1，跳过"
fi

# ---------- 4. .env ----------
if [[ ! -f "${ROOT}/.env" ]]; then
  log_info "env" ".env 不存在，从 .env.example 复制"
  if [[ "${DRY_RUN}" == "1" ]]; then
    log_info "env" "DRY_RUN=1，跳过复制"
  else
    cp "${ROOT}/.env.example" "${ROOT}/.env"
    log_ok "env" ".env 已生成（按需修改）"
  fi
else
  log_warn "env" ".env 已存在，未覆盖"
fi

# ---------- 5. 完成横幅 ----------
cat <<'NEXT' >&2

[next] 接下来手动跑：

    cd seo-2.0
    pip install -r requirements.txt
    uvicorn app.main:app --reload --port 8000

[next] 启动后验证：

    curl http://localhost:8000/api/health
    curl http://localhost:8000/admin/rule-sets/active
    curl -X POST http://localhost:8000/admin/reload
    curl http://localhost:8000/metrics

[next] 把 .env 里的 ai_*_base_url / ai_*_key 填上之后，POST /api/v1/workflow/brief
       才会走 AI；否则自动回退到本地 brief。

NEXT

log_ok "done" "准备就绪；完整日志见 ${LOG_FILE}"
