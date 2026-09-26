# Castor

**The self-hosted habit tracker that takes your data as seriously as your streaks.**

Fast · Private · Security-hardened · Docker-ready in under a minute

![License](https://img.shields.io/badge/license-see%20LICENSE-blue)
![Python](https://img.shields.io/badge/python-3.12%20%7E%203.14-3776AB?logo=python&logoColor=white)
![Astro](https://img.shields.io/badge/astro-7-BC52EE?logo=astro&logoColor=white)
![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)

---

## Why Castor?

Most habit trackers are either cloud services that own your behavioural data, or
lightweight self-hosted apps with a single shared password. **Castor is built for
people who want both**: a fast, modern habit tracker you run yourself — with the
authentication, auditing, and operational polish of a production service.

- 🔐 **Real account security** — Passkeys (WebAuthn), secure password reset,
  rate limiting, and HTTP security headers out of the box.
- 👥 **Circles** — invite-only habit-sharing groups, with email invites
  (self-hosted SMTP; Gmail app-password works out of the box).
- 📋 **Auditability** — Built-in audit logging and security action tracking, so
  you know what happened in your instance and when.
- ⚡ **Real-time by design** — Live tick updates to other signed-in devices
  over WebSockets, backed by a scheduler for recurring jobs and maintenance.
- ♿ **Accessible** — Accessibility is engineered in, not bolted on.
- 🐳 **Deploy anywhere** — Single-container Docker image with health checks;
  the Astro frontend and FastAPI backend ship together.

## Quick start

### Docker (recommended)

```bash
git clone https://github.com/BlueishDragonz/castor.git
cd castor
docker build -t castor:mytag -f docker/Dockerfile .
docker compose up -d
```

Castor is now running with automatic health checks — point your reverse proxy or
browser at the exposed port and create your account. Production data lives in
the `castor_data` volume; real secrets belong in `.user/.secrets.env` inside
that volume, never in the repo.

### Local development (uv + pnpm)

```bash
uv sync
./scripts/dev-up.sh        # backend on :8085 (auto-seeds demo@castor.example.com),
                           # Astro dev on :4321, logs in .user/dev-*.log
pnpm --dir web/concepts install
pnpm --dir web/concepts exec astro check && pnpm --dir web/concepts exec astro build
```

Tests:

```bash
.venv/bin/python -m pytest tests/ --ignore=tests/test_batch4_live.py -q
```

(`tests/test_batch4_live.py` probes a *running* production container — run it
only with an accessible instance and real data expectations.)

## Configuration

Castor is configured entirely through environment variables — copy
[`.env.example`](./.env.example) for local development and see
`castor/configs.py` for the full surface:

| Area | What you configure |
|---|---|
| Core | App secret, database/storage backend, timezone, instance caps |
| Auth | JWT lifetime, passkey rpId/origin, password reset secrets, registration policy |
| Security | Rate limits, cookie/CSP settings via the HTTP security layer |
| Email | SMTP host/credentials for circles invites (empty host = Gmail SSL fallback) |

## Architecture at a glance

```
castor/
├── castor/          # FastAPI backend package
│   ├── app/         # Auth, accounts, audit, security actions, circles, webauthn
│   ├── routes/      # HTTP routes: API, metrics
│   ├── core/  plan/ # Domain logic; plan/ = paid-plan scaffolding (inactive)
│   ├── realtime.py  # WebSocket broadcast layer (live tick updates)
│   └── storage/     # Persistence layer (database or per-user disk)
├── web/concepts/    # Astro 7 + React 19 + shadcn/ui frontend
│   ├── src/pages/   # habits, circles, stats, settings, admin, auth pages…
│   └── src/pages/api/  # Astro BFF endpoints (backendFetch → FastAPI)
├── docker/          # Container build assets (Dockerfile)
├── scripts/         # dev-up.sh / stop-dev.sh
├── tests/           # pytest suite (see pytest.ini) + live-container probes
└── docker-compose.yml  # reference deployment (mirrors the apollo production stack)
```

**Stack:** Python 3.12–3.14 · uv · FastAPI · Astro 7 · React 19 · shadcn/ui ·
pnpm · Docker · pytest

## Security

Security is a feature of this project, not an afterthought. Castor ships with
rate limiting, sanitisation of user-supplied styling, integrity checks, audit
logging, and passkey support enabled from the start. Report vulnerabilities
privately via GitHub Security Advisories — please don't open public issues.

## Roadmap

- [ ] Polished onboarding flow and demo instance
- [ ] Grafana dashboard example for the metrics endpoints
- [ ] Mobile-friendly PWA packaging

## License

See [LICENSE](./LICENSE).

---

*Named after Castor — one half of the Gemini twins. Because good habits work in pairs: the one you track, and the one that tracks you staying honest.*
