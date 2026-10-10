#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo 'Please install uv first: https://docs.astral.sh/uv/getting-started/installation/'; exit 1; }
command -v git >/dev/null || { echo 'Please install Git to enable code repository Wiki'; exit 1; }
export UV_CACHE_DIR="${UV_CACHE_DIR:-/workspace/.cache/uv}"
export UV_LINK_MODE=copy
uv sync --frozen
