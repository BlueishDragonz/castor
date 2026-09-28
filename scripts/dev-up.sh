# Local dev launcher for the castor migration.
#
# Brings up:
#   1. castor backend on 127.0.0.1:8085 (with DEBUG=True → auto-seeds demo)
#   2. Astro dev on 127.0.0.1:4321 (proxies /auth, /users, /webauthn, /health to backend)
#
# Logs to .user/dev-{backend,frontend}.log so you can tail them.
#
# Demo account (auto-seeded in DEBUG mode):
#   email:    demo@castor.example.com
#   password: DemoPass1234!
# (Also: POST /dev/reseed-demo to recover if the demo vanishes.)

set -euo pipefail

cd "$(dirname "$0")/.."

# Ensure .env exists with debug-friendly secrets
if [[ ! -f .env ]]; then
  cat > .env <<'EOF'
DEBUG=true
JWT_SECRET=local-dev-jwt-secret-not-for-production-32chars
RESET_PASSWORD_TOKEN_SECRET=local-dev-reset-secret-not-for-production
NICEGUI_STORAGE_SECRET=local-dev-storage-secret-not-for-production
JWT_LIFETIME_SECONDS=2592000
REQUIRE_ADMIN_FOR_REGISTRATION=false
TRUSTED_LOCAL_EMAIL=
TRUSTED_EMAIL_HEADER=
AUTH_RATE_USER_PER_MINUTE=10000
AUTH_RATE_IP_PER_MINUTE=10000
TIME_ZONE=Europe/London
# The public origin the browser uses. The backend's BrowserOriginMiddleware
# rejects state-changing requests whose Origin/Referer is not in its
# allowlist, and with this unset both the BFF (web/concepts/src/lib/auth.ts)
# and the backend fell back to defaults that did not match, so signing in
# returned 403 "Browser origin not allowed" with nothing visible in the UI.
FRONTEND_URL=http://localhost:4321
EOF
  echo "Wrote .env (DEBUG=true, demo seed will run on startup)"
fi

# FRONTEND_URL is load-bearing for local sign-in, so make sure an existing
# .env has it too rather than silently failing to authenticate.
if ! grep -q '^FRONTEND_URL=' .env 2>/dev/null; then
  echo "FRONTEND_URL=http://localhost:4321" >> .env
  echo "Added FRONTEND_URL to .env (required for local sign-in)"
fi

# The Astro app loads env from web/concepts, NOT the repo root, so the
# value above has to be mirrored there or process.env.FRONTEND_URL is
# undefined inside web/concepts/src/lib/auth.ts. With it undefined the BFF
# sends no Origin, and the backend's BrowserOriginMiddleware rejects every
# state-changing request with 403 "Browser origin not allowed" — sign-in
# fails with no visible cause in the browser.
if ! grep -q '^FRONTEND_URL=' web/concepts/.env 2>/dev/null; then
  echo "FRONTEND_URL=http://localhost:4321" > web/concepts/.env
  echo "Wrote web/concepts/.env with FRONTEND_URL (required for local sign-in)"
fi

mkdir -p .user

# Start backend
echo "→ Starting castor backend on 127.0.0.1:8085 (log: .user/dev-backend.log)"
#
# PUBLIC_URL is exported so the backend derives its WebAuthn relying-party
# ID and expected origin from the same host the browser is served on,
# instead of falling back to a hardcoded default that can drift away
# from reality.
#
# The host MUST be "localhost", not 127.0.0.1, and this is not
# cosmetic. A WebAuthn relying-party ID must be a registrable domain
# suffix; browsers reject a bare IP address. Verified directly in Firefox
# on this host:
#   navigator.credentials.create({ ... rp: { id: '127.0.0.1' } })
#     -> SecurityError: The operation is insecure.
# So a dev setup served on 127.0.0.1 can never complete a passkey
# ceremony. Astro still binds 127.0.0.1; localhost resolves to it, so
# nothing about the binding changes — only the name the browser uses.
PUBLIC_URL=http://localhost:4321 \
FRONTEND_URL=http://localhost:4321 \
  .venv/bin/python -m uvicorn castor.main:app --host 127.0.0.1 --port 8085 --log-level info \
  > .user/dev-backend.log 2>&1 &
BACKEND_PID=$!
echo $BACKEND_PID > .user/dev-backend.pid
echo "  backend PID: $BACKEND_PID"

# Wait for backend health
echo "→ Waiting for backend /health"
for i in $(seq 1 30); do
  if curl -sf --max-time 2 http://127.0.0.1:8085/health > /dev/null 2>&1; then
    echo "  backend healthy"
    break
  fi
  if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo "  backend died, last 30 lines of log:"
    tail -30 .user/dev-backend.log
    exit 1
  fi
  sleep 1
done

# Verify demo user exists
echo "→ Verifying demo user seeded"
DEMO_OK=$(curl -sf -X POST http://127.0.0.1:8085/auth/login \
  -H "content-type: application/x-www-form-urlencoded" \
  --data-urlencode "username=demo@castor.example.com" \
  --data-urlencode "password=DemoPass1234!" 2>&1 || echo "FAIL")
if [[ "$DEMO_OK" == *"access_token"* ]]; then
  echo "  ✓ demo@castor.example.com login succeeded"
else
  echo "  ⚠ demo login failed (probably not seeded yet — try POST /dev/reseed-demo):"
  echo "$DEMO_OK" | head -3
fi

# Start Astro
echo "→ Starting Astro dev on 127.0.0.1:4321 (log: .user/dev-frontend.log)"
cd web/concepts
# FRONTEND_URL must be exported to the Astro process, not merely written
# to .env files. lib/auth.ts's serverEnv() reads process.env only, by
# design: an `import.meta.env` fallback makes Vite inline the whole
# build-time env object into the emitted SSR bundle. Without this
# export, publicOrigin() resolves to '' in dev and the backend rejects
# every state-changing request with 403 "Browser origin not allowed".
BACKEND_URL=http://127.0.0.1:8085 \
  PUBLIC_BACKEND_URL=http://127.0.0.1:8085 \
  FRONTEND_URL=http://localhost:4321 \
  PUBLIC_URL=http://localhost:4321 \
  pnpm exec astro dev --port 4321 --host 127.0.0.1 \
  > ../../.user/dev-frontend.log 2>&1 &
FRONTEND_PID=$!
echo $FRONTEND_PID > ../../.user/dev-frontend.pid
echo "  frontend PID: $FRONTEND_PID"
cd ../..

echo "→ Waiting for Astro to come up"
for i in $(seq 1 30); do
  if curl -sf --max-time 2 http://localhost:4321/ -o /dev/null 2>&1; then
    echo "  ✓ Astro serving /"
    break
  fi
  sleep 1
done

cat <<EOF

────────────────────────────────────────────────────────
✓ Local dev up

  Backend   http://127.0.0.1:8085   (log: .user/dev-backend.log)
  Frontend  http://localhost:4321   (log: .user/dev-frontend.log)

Demo account:
  email     demo@castor.example.com
  password  DemoPass1234!

To inspect from this machine:  open http://localhost:4321/
To inspect from the LAN:        set up a tunnel (see scripts/tunnel-dev.sh) or
                               use ssh -L 4321:127.0.0.1:4321 joel@laptop

Stop with:                      scripts/stop-dev.sh
────────────────────────────────────────────────────────
EOF
