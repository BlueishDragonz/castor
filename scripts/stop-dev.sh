#!/usr/bin/env bash
# Stop the local dev servers started by dev-up.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

for svc in dev-backend dev-frontend; do
  pidfile=".user/${svc}.pid"
  if [[ -f "$pidfile" ]]; then
    pid=$(cat "$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
      echo "stopping $svc (pid $pid)"
      kill "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
  fi
done
# Also kill anything still bound to 8085/4321 just in case
for port in 8085 4321; do
  pids=$(lsof -nP -iTCP:$port -sTCP:LISTEN -t 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo "killing leftover listeners on :$port → $pids"
    kill $pids 2>/dev/null || true
  fi
done
echo "done"
