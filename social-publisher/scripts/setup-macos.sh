#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

for command in python3 node npm docker; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "Missing required command: $command" >&2
    exit 1
  }
done

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r backend/requirements.txt

if [[ ! -f .env ]]; then
  cp .env.example .env
  CONNECTOR_KEY="$(
    .venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
  )"
  EXECUTOR_KEY="$(
    .venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))'
  )"
  .venv/bin/python - "$CONNECTOR_KEY" "$EXECUTOR_KEY" <<'PY'
from pathlib import Path
import sys

path = Path(".env")
value = path.read_text(encoding="utf-8")
value = value.replace("CONNECTOR_SECRET_KEY=", f"CONNECTOR_SECRET_KEY={sys.argv[1]}", 1)
value = value.replace(
    "SOCIAL_EXECUTOR_SHARED_SECRET=",
    f"SOCIAL_EXECUTOR_SHARED_SECRET={sys.argv[2]}",
    1,
)
path.write_text(value, encoding="utf-8")
PY
fi

set -a
source .env
set +a

docker compose up -d postgres
for _ in {1..40}; do
  if docker compose exec -T postgres pg_isready -U publisher -d social_publisher >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker compose exec -T postgres pg_isready -U publisher -d social_publisher

.venv/bin/python scripts/init_db.py
(cd executor && npm ci)
(cd frontend && npm ci)

echo "Setup complete. Run: bash scripts/start-macos.sh"
