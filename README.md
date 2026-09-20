# Castor

**The self-hosted habit tracker that takes your data as seriously as your streaks.**

Fast · Private · Security-hardened · Docker-ready in under a minute

![License](https://img.shields.io/badge/license-see%20LICENSE-blue)
![Python](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)

---

## Why Castor?

Most habit trackers are either cloud services that own your behavioural data, or
lightweight self-hosted apps with a single shared password. **Castor is built for
people who want both**: a fast, modern habit tracker you run yourself — with the
authentication, auditing, and operational polish of a production service.

- 🔐 **Real account security** — Passkeys (WebAuthn), Google One Tap sign-in,
  secure password reset, rate limiting, and HTTP security headers out of the box.
- 📋 **Auditability** — Built-in audit logging and security action tracking, so you
  know what happened in your instance and when.
- 🏆 **Challenges** — Turn habits into shared or personal challenges with defined
  goals, not just endless streak counting.
- ⚡ **Real-time by design** — Live updates over your data, backed by a scheduler
  for recurring jobs, reminders, and maintenance tasks.
- 📊 **Observable** — First-class metrics endpoints and health checks; drop it
  straight into your Prometheus/Grafana or uptime monitor.
- ♿ **Accessible** — Accessibility is engineered in, not bolted on.
- 🐳 **Deploy anywhere** — One-command Docker setup, plus a ready-made Fly.io
  configuration for cheap global hosting.

## Quick start

### Docker (recommended)

```bash
git clone https://github.com/BlueishDragonz/castor.git
cd castor

cp .env.example .env   # configure secrets & sign-in providers (see Configuration)
docker compose up -d
```

Castor is now running with automatic health checks — point your reverse proxy or
browser at the exposed port and create your account.

### Fly.io (hosted, still yours)

```bash
fly launch --config fly.toml
fly secrets set APP_SECRET=...   # plus any provider keys
fly deploy
```

### Local development (uv)

```bash
uv sync
uv run pytest          # run the test suite
uv run ./start.sh      # run the app locally
```

## Configuration

Castor is configured entirely through environment variables — copy `.env.example`
and set:

| Area | What you configure |
|---|---|
| Core | App secret, database/storage backend, timezone |
| Auth | Enable passkeys, Google One Tap client ID, password policy |
| Security | Rate limits, cookie/CSP settings via the HTTP security layer |
| Observability | Metrics endpoint, log level, health-check behaviour |

## Architecture at a glance

```
castor/
├── beaverhabits/          # Application package
│   ├── app/               # Auth, accounts, security actions, audit, challenges
│   ├── routes/            # HTTP routes: API, metrics, astro, Google One Tap
│   ├── frontend/          # UI layer
│   ├── core/  plan/       # Domain logic and planning features
│   └── storage/           # Persistence layer
├── docker/                # Container build assets
├── tests/                 # pytest suite (see pytest.ini)
├── docker-compose.yml     # Self-host in one command
├── fly.toml               # Fly.io deployment config
└── openclaw/SKILL.md      # Agent skill for AI-assisted workflows
```

**Stack:** Python 3.12+ · uv · Docker · Fly.io · pytest

## Security

Security is a feature of this project, not an afterthought. Castor ships with
rate limiting, sanitisation of user-supplied styling, integrity checks, audit
logging, and passkey support enabled from the start. Report vulnerabilities
privately via GitHub Security Advisories — please don't open public issues.

## Roadmap

- [ ] Polished onboarding flow and demo instance
- [ ] More challenge types and templates
- [ ] Grafana dashboard example for the metrics endpoints
- [ ] Mobile-friendly PWA packaging

## Contributing

Issues and pull requests are welcome. Please run `uv run pytest` before opening a
PR, and keep security-related changes well documented.

## License

See [LICENSE](./LICENSE).

---

*Named after Castor — one half of the Gemini twins. Because good habits work in pairs: the one you track, and the one that tracks you staying honest.*
