#!/bin/sh
# Container entrypoint / supervisor.
#
# F2 — why this file exists instead of an inline `sh -c` in CMD:
#
# The previous CMD was:
#   sh -c "gunicorn ... & BACKEND_PID=$!; trap 'kill $BACKEND_PID' EXIT INT TERM;
#          exec node ..."
#
# `exec` REPLACES the shell, so the shell — and the trap it had registered —
# was discarded. `docker stop` sent SIGTERM to node (now PID 1), node exited,
# the container tore down, and gunicorn was killed by the kernel with SIGKILL
# rather than SIGTERM. Gunicorn therefore never ran its shutdown hook, so the
# pending habit-list writes held in memory were discarded — writes the API
# had already acknowledged to the user. Reproduced in a sandbox by the audit.
#
# The fix has two halves, and both are required:
#   1. This script does NOT exec. It stays alive as PID 1, so the trap is real
#      and `docker stop` reaches gunicorn.
#   2. tini (below) is PID 1. A shell as PID 1 does not reap orphaned children,
#      so a worker that dies leaves a zombie and the container's PID count
#      creeps up. tini reaps and forwards signals.
set -e

BACKEND_PID=""

# Forward termination to gunicorn and wait for it to finish its own shutdown.
# SIGTERM (not SIGKILL) is what lets the app flush pending writes.
shutdown() {
    if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo "entrypoint: forwarding SIGTERM to gunicorn (pid $BACKEND_PID)"
        kill -TERM "$BACKEND_PID" 2>/dev/null || true
        # Give it a bounded window to flush; escalate only if it hangs.
        i=0
        while [ "$i" -lt 25 ] && kill -0 "$BACKEND_PID" 2>/dev/null; do
            sleep 1
            i=$((i + 1))
        done
        if kill -0 "$BACKEND_PID" 2>/dev/null; then
            echo "entrypoint: gunicorn did not exit in time; sending SIGKILL"
            kill -KILL "$BACKEND_PID" 2>/dev/null || true
        fi
    fi
}
trap shutdown TERM INT

# The app persists habit data on SIGTERM via the lifespan shutdown hook in
# castor/main.py. Give gunicorn the full window to drain before starting the
# frontend, so a slow flush is not cut short.
gunicorn castor.main:app \
    --bind 127.0.0.1:8081 \
    -w 1 \
    -k uvicorn_worker.UvicornWorker \
    --max-requests 10000 \
    --graceful-timeout 30 \
    --timeout 60 \
    --log-level info &
BACKEND_PID=$!

# Run the Astro server in the foreground. No `exec`: this shell must survive
# to run the trap above.
node web/concepts/dist/server/entry.mjs &
FRONTEND_PID=$!

# Exit as soon as EITHER process exits, so a crashed backend takes the whole
# container down and `restart: unless-stopped` can cycle it, rather than
# leaving a live frontend proxying to a dead backend.
wait -n "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || wait "$BACKEND_PID" "$FRONTEND_PID"
EXIT_CODE=$?

echo "entrypoint: a service process exited (code $EXIT_CODE); shutting down"
shutdown
exit "$EXIT_CODE"
