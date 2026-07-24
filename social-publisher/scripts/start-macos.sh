#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[[ -f .env ]] || {
  echo "Missing .env. Run: bash scripts/setup-macos.sh" >&2
  exit 1
}

set -a
source .env
set +a

pids=()
cleanup() {
  for pid in "${pids[@]:-}"; do
    kill "$pid" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT INT TERM

.venv/bin/uvicorn app.main:app \
  --app-dir backend --host 127.0.0.1 --port 8000 >backend.log 2>&1 &
pids+=("$!")

(cd executor && npm start) >executor.log 2>&1 &
pids+=("$!")

(cd frontend && npm run dev -- --host 127.0.0.1) >frontend.log 2>&1 &
pids+=("$!")

for _ in {1..40}; do
  if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -fsS http://127.0.0.1:8000/api/health >/dev/null

open http://127.0.0.1:5173/
echo "Social publisher is running. Press Ctrl+C to stop."
wait
