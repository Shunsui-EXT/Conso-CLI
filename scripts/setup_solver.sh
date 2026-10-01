#!/usr/bin/env bash
# Clone + set up the captcha-solver sidecar (waguriagentic/captcha-solver).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/vendor/captcha-solver"
REPO="${CAPTCHA_SOLVER_REPO:-https://github.com/waguriagentic/captcha-solver.git}"

if [ ! -d "$DEST/.git" ]; then
    mkdir -p "$ROOT/vendor"
    git clone --depth 1 "$REPO" "$DEST"
else
    echo "solver already present at $DEST"
fi

# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"
pip install -r "$DEST/requirements.txt"

echo "solver ready. start with: bash scripts/start_solver.sh"
