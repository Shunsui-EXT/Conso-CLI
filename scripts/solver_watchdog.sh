#!/usr/bin/env bash
# Run the captcha-solver sidecar with a watchdog: restart it if /health stops
# answering or if recent solves are all timing out (the Cloudflare-flag state
# that a plain restart clears).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/logs/solver.log"
HEALTH="http://127.0.0.1:8877/health"
CHECK_INTERVAL="${WATCHDOG_INTERVAL:-20}"
MAX_FAILS="${WATCHDOG_MAX_FAILS:-3}"

mkdir -p "$ROOT/logs"
fails=0

start_solver() {
    # Kill by port holder + xvfb wrapper. Matching on the cmdline is unsafe:
    # the process runs as bare `python3 server.py`, so a
    # "captcha-solver/server.py" pattern never matches and the stale process
    # keeps the port (all "restarts" silently no-op).
    local holder
    holder=$(ss -ltnp 2>/dev/null | grep ":8877" | grep -oP 'pid=\K[0-9]+' | head -1)
    [ -n "$holder" ] && kill -9 "$holder" 2>/dev/null
    pkill -9 -f "xvfb-run.*server.py" 2>/dev/null
    pkill -9 -f "python3 server.py" 2>/dev/null
    sleep 2
    # shellcheck disable=SC1091
    source "$ROOT/.venv/bin/activate"
    ( cd "$ROOT/vendor/captcha-solver" && \
      BROWSER_HEADLESS="${BROWSER_HEADLESS:-0}" PORT="${PORT:-8877}" \
      nohup xvfb-run -a --server-args="-screen 0 1920x1080x24" \
        python3 server.py >> "$LOG" 2>&1 & )
    echo "[watchdog] solver started, waiting for health..."
    for _ in $(seq 1 20); do
        sleep 2
        if curl -sS --max-time 4 "$HEALTH" -o /dev/null 2>/dev/null; then
            echo "[watchdog] solver healthy"
            return 0
        fi
    done
    echo "[watchdog] solver did not become healthy"
    return 1
}

start_solver

while true; do
    sleep "$CHECK_INTERVAL"
    if ! curl -sS --max-time 5 "$HEALTH" -o /dev/null 2>/dev/null; then
        fails=$((fails + 1))
        echo "[watchdog] health check failed ($fails/$MAX_FAILS)"
    else
        # detect a flag state: last N solves all timed out
        recent=$(tail -40 "$LOG" 2>/dev/null | grep -c "408 Request Timeout")
        clicked=$(tail -40 "$LOG" 2>/dev/null | grep -c "checkbox clicked")
        if [ "$recent" -ge 4 ] && [ "$clicked" -ge 3 ]; then
            fails=$((fails + 1))
            echo "[watchdog] flag detected: $recent timeouts / $clicked clicks ($fails/$MAX_FAILS)"
        else
            fails=0
        fi
    fi
    if [ "$fails" -ge "$MAX_FAILS" ]; then
        echo "[watchdog] restarting solver"
        fails=0
        start_solver
    fi
done
