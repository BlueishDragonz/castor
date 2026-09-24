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
EOF
  echo "Wrote .env (DEBUG=true, demo seed will run on startup)"
fi

mkdir -p .user

# Start backend
echo "→ Starting castor backend on 127.0.0.1:8085 (log: .user/dev-backend.log)"
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
BACKEND_URL=http://127.0.0.1:8085 \
  PUBLIC_BACKEND_URL=http://127.0.0.1:8085 \
  pnpm exec astro dev --port 4321 --host 127.0.0.1 \
  > ../../.user/dev-frontend.log 2>&1 &
FRONTEND_PID=$!
echo $FRONTEND_PID > ../../.user/dev-frontend.pid
echo "  frontend PID: $FRONTEND_PID"
cd ../..

echo "→ Waiting for Astro to come up"
for i in $(seq 1 30); do
  if curl -sf --max-time 2 http://127.0.0.1:4321/ -o /dev/null 2>&1; then
    echo "  ✓ Astro serving /"
    break
  fi
  sleep 1
done

cat <<EOF

────────────────────────────────────────────────────────
✓ Local dev up

  Backend   http://127.0.0.1:8085   (log: .user/dev-backend.log)
  Frontend  http://127.0.0.1:4321   (log: .user/dev-frontend.log)

Demo account:
  email     demo@castor.example.com
  password  DemoPass1234!

To inspect from this machine:  open http://127.0.0.1:4321/
To inspect from the LAN:        set up a tunnel (see scripts/tunnel-dev.sh) or
                               use ssh -L 4321:127.0.0.1:4321 joel@laptop

Stop with:                      scripts/stop-dev.sh
────────────────────────────────────────────────────────
EOF
