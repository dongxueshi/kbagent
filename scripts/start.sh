#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
test -x .venv/bin/python || { echo 'Run bash scripts/setup.sh first.'; exit 1; }
exec .venv/bin/python -m uvicorn kbagent.main:app --host 127.0.0.1 --port "${KBAGENT_PORT:-8000}"
