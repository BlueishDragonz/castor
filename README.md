# Castor

A self-hosted habit tracker. Fork of
[daya0576/beaverhabits](https://github.com/daya0576/beaverhabits) that adds
passkey support.

## What's different

- **Passkey login** (USB key, iCloud Keychain, 1Password)
- **Email-first login flow** with a Security page to manage passkeys
- **Mobile-first layout** — bottom nav, Inter font, phone-class UA detection

## Run it

```yaml
services:
  castor:
    image: castor:custom-2026-09-14
    network_mode: "service:wg-easy"
    environment:
      - HABITS_STORAGE=DATABASE
      - WEBAUTHN_RP_ID=10.8.0.1
      - WEBAUTHN_ORIGIN=http://10.8.0.1:8080
    volumes:
      - castor_data:/app/.user/
    restart: unless-stopped
volumes:
  castor_data:
```

Build from source:

```bash
git clone https://github.com/BlueishDragonz/castor
cd castor && git checkout custom/recovery-2026-09-14
docker build -f docker/Dockerfile -t castor:custom-2026-09-14 .
```

## License

AGPL-3.0 — inherited from upstream.
