FROM python:3.14-slim AS python-base
ENV UV_COMPILE_BYTECODE=1 \
    \
    PYSETUP_PATH="/opt/pysetup" \
    VENV_PATH="/opt/pysetup/.venv"
ENV PATH="$VENV_PATH/bin:$PATH"


FROM python-base AS builder-base
RUN apt-get update && apt-get install --no-install-recommends -y \
        build-essential \
        libffi-dev \
        libssl-dev \
        curl \
        ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
WORKDIR $PYSETUP_PATH
ADD https://astral.sh/uv/0.5.26/install.sh /uv-installer.sh
RUN sh /uv-installer.sh && rm /uv-installer.sh
ENV PATH="/root/.local/bin/:$PATH"

COPY uv.lock pyproject.toml ./
RUN uv sync --frozen --no-install-project --no-dev -v

# [Experimental] Remove unused nicegui libs
ENV NICEGUI_LIB_PATH="$VENV_PATH/lib/python*/site-packages/nicegui/elements/lib"
RUN for path in $(ls -d $NICEGUI_LIB_PATH); do \
    rm -rf "$path/mermaid/" && \
    rm -rf "$path/plotly/" && \
    rm -rf "$path/vanilla-jsoneditor/"; \
done


# ─────────────────────────────────────────────────────────────────────
# Astro build stage — produces web/concepts/dist/ + node_modules tree
# for the @astrojs/node standalone server entry.
#
# The monorepo-image strategy (user decision 2026-09-24, option A):
# one image runs both the Python backend (gunicorn on 127.0.0.1:8081)
# and the Astro frontend (node on 0.0.0.0:8080). The browser only ever
# hits :8080; backend access from Astro happens via backendFetch()
# targeting BACKEND_URL=http://127.0.0.1:8081. No middleware proxy
# needed because no client-side fetch goes to /auth, /users, or
# /webauthn directly — every browser-facing path is either an Astro
# page or an Astro BFF route in pages/api/.
#
# Node 22.12.0 is the minimum required by @astrojs/node@^11 + astro@^7
# per web/concepts/package.json engines.
# ─────────────────────────────────────────────────────────────────────
FROM node:22.12-bookworm-slim AS astro-builder
WORKDIR /build
RUN corepack enable
COPY web/concepts/package.json web/concepts/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/concepts ./
RUN pnpm exec astro build


FROM python-base AS production
EXPOSE 8080
COPY --from=builder-base $PYSETUP_PATH $PYSETUP_PATH
WORKDIR /app
COPY start.sh .
COPY castor ./castor
COPY statics ./statics
COPY healthcheck.py .

# Astro build output + node_modules tree (the standalone server entry
# imports from node_modules at runtime via relative paths).
COPY --from=astro-builder /build/dist /app/web/concepts/dist
COPY --from=astro-builder /build/node_modules /app/web/concepts/node_modules
COPY --from=astro-builder /build/package.json /app/web/concepts/package.json

# Backend listens on 8081 (internal only); Astro listens on 8080 (the
# published port). BACKEND_URL points Astro at the local backend so
# backendFetch() in Astro pages and BFF routes can talk to it.
ENV BACKEND_URL=http://127.0.0.1:8081 \
    HOST=0.0.0.0 \
    PORT=8080

RUN chmod -R g+w /app && \
    chown -R nobody /app
USER nobody

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD python healthcheck.py

# Supervisor pattern: gunicorn (backend, internal :8081) in the
# background, Astro (frontend, public :8080) on PID 1. trap forwards
# SIGTERM to gunicorn so `docker stop` cleanly terminates both.
#
# `exec` on the Astro node process gives it PID 1 so it receives
# signals directly; the gunicorn background process is tracked via
# $BACKEND_PID for the trap.
CMD ["sh", "-c", "gunicorn castor.main:app --bind 127.0.0.1:8081 -w 1 -k uvicorn_worker.UvicornWorker --max-requests 10000 --log-level info & BACKEND_PID=$!; trap 'kill $BACKEND_PID 2>/dev/null' EXIT INT TERM; exec node web/concepts/dist/server/entry.mjs"]
