#!/usr/bin/env bash
# Start the captcha-solver sidecar (headed under Xvfb) on :8877.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/vendor/captcha-solver"
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"
export BROWSER_HEADLESS="${BROWSER_HEADLESS:-0}"
export PORT="${PORT:-8877}"
mkdir -p "$ROOT/logs"
exec xvfb-run -a --server-args="-screen 0 1920x1080x24" \
    python3 server.py >> "$ROOT/logs/solver.log" 2>&1
