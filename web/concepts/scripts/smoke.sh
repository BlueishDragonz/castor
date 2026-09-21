#!/usr/bin/env bash
#
# Smoke test for the castor Astro app.
#
# Verifies the first acceptance criterion the user set:
#   "castor can run and be accessed via browser."
#
# Boots astro dev, waits for it to listen, then curls the home and login
# pages and asserts:
#   - HTTP 200 on both routes
#   - <title> is "Castor"
#   - Design tokens (--castor-card-radius etc.) appear inline in the HTML
#   - shadcn Button utility classes (bg-primary, rounded-md) are compiled
#   - No "beaver" strings remain (regression guard for the rename pass)
#
# Run with: bash web/concepts/scripts/smoke.sh
#
# This script exits non-zero on the first failure, so CI can pick it up
# directly.
#
set -euo pipefail

cd "$(dirname "$0")/.."

PORT="${PORT:-4321}"
HOST="${HOST:-127.0.0.1}"
BASE="http://${HOST}:${PORT}"

echo "› starting astro dev on ${BASE}..."
LOG=$(mktemp)
BACKEND_URL="http://localhost:8080" pnpm exec astro dev \
  --port "${PORT}" --host "${HOST}" >"${LOG}" 2>&1 &
DEV_PID=$!

cleanup() {
  kill "${DEV_PID}" 2>/dev/null || true
  wait "${DEV_PID}" 2>/dev/null || true
}
trap cleanup EXIT

# Wait for the server to accept connections (max 30s)
for i in $(seq 1 60); do
  if curl -sS -o /dev/null "${BASE}/" 2>/dev/null; then
    echo "› dev server up after ${i} attempts"
    break
  fi
  sleep 0.5
  if [ "${i}" -eq 60 ]; then
    echo "✘ dev server failed to come up within 30s. tail of log:"
    tail -30 "${LOG}" || true
    exit 1
  fi
done

fail() { echo "✘ $1"; exit 1; }
pass() { echo "✓ $1"; }

# Helper: fetch + run assertions
check_page() {
  local route="$1"
  local title="$2"

  echo "› checking ${route}"
  local body status
  body=$(curl -sS -o /tmp/page.html -w "%{http_code}" "${BASE}${route}") \
    || fail "could not fetch ${route}"
  status="${body}"

  [ "${status}" = "200" ] \
    || fail "${route} returned HTTP ${status}, expected 200"

  grep -q "<title>${title}</title>" /tmp/page.html \
    || fail "${route} missing <title>${title}</title>"

  grep -q 'var(--castor-card-radius)' /tmp/page.html \
    || fail "${route} missing --castor-card-radius CSS variable"

  grep -q 'var(--castor-emblem-radius)' /tmp/page.html \
    || fail "${route} missing --castor-emblem-radius CSS variable"

  grep -q 'bg-primary' /tmp/page.html \
    || fail "${route} missing shadcn bg-primary utility class"

  grep -q 'rounded-md' /tmp/page.html \
    || fail "${route} missing Tailwind rounded-md utility class"

  if grep -qi "beaver" /tmp/page.html; then
    fail "${route} contains a 'beaver' string (regression of the castor rename)"
  fi

  pass "${route} (HTTP 200, tokens live, no beaver strings)"
}

check_page "/" "Castor"
check_page "/login" "Sign in — Castor"

echo ""
echo "✓ smoke test passed — castor Astro app is reachable in a browser."
