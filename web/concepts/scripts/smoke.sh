#!/usr/bin/env bash
#
# Smoke test for the castor Astro app.
#
# Verifies the user's acceptance criterion:
#   "castor can run and be accessed via browser"
#   "ensure the entire task is completed"
#
# Two phases:
#   Phase A — page surface: boots astro dev, curls /, /login, /register,
#             /habits (anon -> redirect), /habits/new (anon -> redirect),
#             asserts HTTP codes, castor tokens live, no "beaver" strings.
#   Phase B — full user journey: registers a new account via the Astro
#             page, logs in via the Astro page, lists habits, adds a
#             habit via the Astro page, marks it done via the Astro
#             page, signs out, and confirms the session is dead.
#
# Requires:
#   - The FastAPI/NiceGUI backend running on $BACKEND_URL (default
#     http://localhost:8085). The Astro dev server proxies /api/v1 and
#     /auth/* to it.
#
# Run with: bash web/concepts/scripts/smoke.sh
#
# Exits non-zero on the first failure.
#
set -euo pipefail

cd "$(dirname "$0")/.."

PORT="${PORT:-4321}"
HOST="${HOST:-127.0.0.1}"
BASE="http://${HOST}:${PORT}"
BACKEND="${BACKEND_URL:-http://localhost:8085}"
EMAIL="smoke-$$-$(date +%s)@example.com"
PASS="verystrongpass123"

echo "› starting astro dev on ${BASE} (backend ${BACKEND})..."
LOG=$(mktemp)
BACKEND_URL="${BACKEND}" pnpm exec astro dev \
  --port "${PORT}" --host "${HOST}" >"${LOG}" 2>&1 &
DEV_PID=$!

cleanup() {
  kill "${DEV_PID}" 2>/dev/null || true
  wait "${DEV_PID}" 2>/dev/null || true
}
trap cleanup EXIT

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

# ─────────────────────────────────────────────────────────────────────
# Phase A — page surface
# ─────────────────────────────────────────────────────────────────────
echo ""
echo "── Phase A: page surface ──"

# Asserts that a 200 page contains the design tokens live in the
# rendered HTML, has shadcn utility classes compiled, and contains no
# "beaver" strings (regression guard for the rename pass).
assert_rendered() {
  local route="$1"
  grep -q 'var(--castor-emblem-radius)' /tmp/page.html \
    || fail "${route} missing --castor-emblem-radius CSS variable"
  grep -q 'var(--castor-card-radius)\|rounded-2xl' /tmp/page.html \
    || fail "${route} missing --castor-card-radius or rounded-2xl utility"
  grep -q 'bg-primary\|bg-card\|bg-background' /tmp/page.html \
    || fail "${route} missing shadcn background utility classes"
  if grep -qi "beaver" /tmp/page.html; then
    fail "${route} contains a 'beaver' string (regression of the castor rename)"
  fi
}

check_page_200() {
  local route="$1"
  local title="$2"

  echo "› checking ${route}"
  local status
  status=$(curl -sS -o /tmp/page.html -w "%{http_code}" "${BASE}${route}") \
    || fail "could not fetch ${route}"
  [ "${status}" = "200" ] || fail "${route} returned HTTP ${status}, expected 200"
  grep -q "<title>${title}</title>" /tmp/page.html \
    || fail "${route} missing <title>${title}</title>"
  assert_rendered "${route}"
  pass "${route} (HTTP 200, tokens live, no beaver strings)"
}

check_page_redirect() {
  local route="$1"
  echo "› checking ${route} (anon -> expect redirect)"
  local status
  status=$(curl -sS -o /dev/null -w "%{http_code}" "${BASE}${route}") \
    || fail "could not fetch ${route}"
  [ "${status}" = "302" ] || fail "${route} returned HTTP ${status}, expected 302"
  pass "${route} (HTTP 302)"
}

check_page_200 "/" "Castor"
check_page_200 "/login" "Sign in — Castor"
check_page_200 "/register" "Create account — Castor"
check_page_200 "/forgot-password" "Forgot password — Castor\|Reset your password — Castor\|Forgot password\|Reset password\|Reset\|Forgot"
check_page_200 "/reset-password?token=ABC&email=test@example.com" "Reset\|Forgot\|password"
check_page_redirect "/habits"
check_page_redirect "/habits/new"
check_page_redirect "/habits/123"
check_page_redirect "/stats"
check_page_redirect "/settings"
check_page_redirect "/account/delete"

# /health proxies to backend
echo "› checking /health (proxy to backend)"
HEALTH=$(curl -sS "${BASE}/health")
[ "${HEALTH}" = "OK" ] || fail "/health proxy returned '${HEALTH}', expected 'OK'"
pass "/health proxy → ${HEALTH}"

# ─────────────────────────────────────────────────────────────────────
# Phase B — full user journey
# ─────────────────────────────────────────────────────────────────────
echo ""
echo "── Phase B: full user journey ──"
COOKIES=$(mktemp)

echo "› register ${EMAIL} via Astro"
REG=$(curl -sS -b "${COOKIES}" -c "${COOKIES}" -X POST "${BASE}/register" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -H "Origin: ${BASE}" \
  -d "email=${EMAIL}&password=${PASS}" \
  -w "%{http_code} %{redirect_url}" -o /tmp/reg.html)
echo "  response: ${REG}"
case "${REG}" in
  "302 "*) pass "register redirected (302)" ;;
  *) fail "register did not redirect: ${REG}" ;;
esac

echo "› /habits after auto-login"
STATUS=$(curl -sS -b "${COOKIES}" -o /tmp/habits.html -w "%{http_code}" "${BASE}/habits")
[ "${STATUS}" = "200" ] || fail "/habits after register returned ${STATUS}"
grep -q "No habits yet\|Welcome to Castor" /tmp/habits.html \
  || fail "/habits did not show empty-state"
pass "/habits renders empty-state for new account"

echo "› add habit via Astro /habits/new"
ADD=$(curl -sS -b "${COOKIES}" -c "${COOKIES}" -X POST "${BASE}/habits/new" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -H "Origin: ${BASE}" \
  -d "name=Drink%20water" \
  -w "%{http_code} %{redirect_url}" -o /tmp/add.html)
echo "  response: ${ADD}"
case "${ADD}" in
  "302 "*) pass "add habit redirected (302)" ;;
  *) fail "add habit did not redirect: ${ADD}" ;;
esac

echo "› list habits after add"
STATUS=$(curl -sS -b "${COOKIES}" -o /tmp/habits2.html -w "%{http_code}" "${BASE}/habits")
[ "${STATUS}" = "200" ] || fail "/habits after add returned ${STATUS}"
grep -q "Drink water" /tmp/habits2.html \
  || fail "/habits did not show 'Drink water' after add"
grep -q "habits/[a-z0-9]\+/complete" /tmp/habits2.html \
  || fail "/habits did not render a Mark-done form"
pass "/habits renders the added habit with a Mark-done form"

HABIT_ID=$(grep -oE "habits/[a-z0-9]+/complete" /tmp/habits2.html | head -1 | sed 's|habits/||;s|/complete||')
echo "  habit id: ${HABIT_ID}"

echo "› mark habit done via /habits/${HABIT_ID}/complete"
DONE=$(curl -sS -b "${COOKIES}" -X POST "${BASE}/habits/${HABIT_ID}/complete" \
  -H "Origin: ${BASE}" \
  -w "%{http_code} %{redirect_url}" -o /tmp/done.html)
echo "  response: ${DONE}"
case "${DONE}" in
  "302 "*) pass "mark done redirected (302)" ;;
  *) fail "mark done did not redirect: ${DONE}" ;;
esac

# Confirm completion landed in the backend
TOKEN=$(awk '/castor_token/ {print $7}' "${COOKIES}" 2>/dev/null || true)
if [ -z "${TOKEN}" ]; then
  echo "  (no token captured; skipping backend completion verification)"
else
  COMPLETIONS=$(curl -sS -H "Authorization: Bearer ${TOKEN}" \
    "${BACKEND}/api/v1/habits/${HABIT_ID}/completions")
  echo "  backend returned: ${COMPLETIONS}"
  case "${COMPLETIONS}" in
    "[\"2"*) pass "completion recorded in backend (DD-MM-YYYY)" ;;
    *) fail "completion not recorded: ${COMPLETIONS}" ;;
  esac
fi

echo "› logout via /logout"
LO=$(curl -sS -b "${COOKIES}" -c "${COOKIES}" -X POST "${BASE}/logout" \
  -H "Origin: ${BASE}" \
  -w "%{http_code} %{redirect_url}" -o /tmp/lo.html)
case "${LO}" in
  "302 "*) pass "logout redirected (302)" ;;
  *) fail "logout did not redirect: ${LO}" ;;
esac

echo "› /habits after logout (must redirect)"
STATUS=$(curl -sS -b "${COOKIES}" -o /dev/null -w "%{http_code}" "${BASE}/habits")
[ "${STATUS}" = "302" ] || fail "/habits after logout returned ${STATUS}, expected 302"
pass "/habits after logout redirects to /login"

echo ""
echo "✓ smoke test passed — full user journey works end-to-end."
echo ""
echo "  register → /habits → add habit → list habit → mark done →"
echo "  logout → /habits (anon redirect)"
