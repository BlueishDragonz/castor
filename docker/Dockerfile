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
# F20: was node:22.12-bookworm-slim, a December 2024 build. That binary is
# copied verbatim into production and runs as PID 1 on the public port, so a
# ~21-month-stale runtime meant missing every Node 22.x security release
# accumulated since. Pinned to the current 22.x LTS line (Jod); staying on 22
# rather than jumping to 24 keeps this a patch-level runtime change instead of
# a major-version migration in the same deploy.
FROM node:22.23-bookworm-slim AS astro-builder
WORKDIR /build
# Install pnpm directly via npm instead of corepack. node:22.12's
# bundled corepack has a stale npm signing-key keyring and hard-fails
# ("Cannot find matching keyid") when fetching pnpm from the registry
# after npm's 2026 key rotation. npm treats registry signatures as
# advisory and installs fine; pinning pnpm@12.5.1 matches the local
# dev toolchain (lockfileVersion '9.0') and makes the image build
# deterministic. Trade-off: no corepack "known good versions"
# protection — accepted because corepack itself is what is broken.
RUN npm install -g pnpm@12.5.1
# pnpm-workspace.yaml is REQUIRED in the install layer: pnpm ≥10 no
# longer reads the "pnpm" field from package.json (prints "no longer
# read" warning), and the build approvals (allowBuilds: esbuild,
# @tailwindcss/oxide, sharp) live in the workspace file. Without it,
# pnpm 12 hard-fails with ERR_PNPM_IGNORED_BUILDS on esbuild.
# Verified in a clean-dir repro before patching (2026-09-25).
COPY web/concepts/package.json web/concepts/pnpm-lock.yaml web/concepts/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
# F19: .dockerignore now excludes node_modules, but never rely on a single
# ignore file for supply-chain integrity — a developer editing it, or a build
# invoked with a different context, would silently ship an unverified
# dependency tree. Delete any node_modules that arrived with the context before
# building, so the only dependency tree in the image is the one pnpm just
# installed from the committed lockfile.
COPY web/concepts ./
RUN rm -rf node_modules && pnpm install --frozen-lockfile
# BACKEND_URL as a BUILD env (declared after the cached dependency
# layers so changing it doesn't re-run pnpm install): Vite inlines
# import.meta.env.* at build time (unlike process.env, read at
# runtime). ~15 server files (pages/api/v1/habits*.ts,
# habits/[id].astro, tokens.astro, ...) use
# import.meta.env.BACKEND_URL and silently fell back to dev's
# localhost:8085 in the built image — the habits BFF 500'd in the
# Phase-4 Docker smoke until this was set. Value matches the
# production-stage ENV and docker-compose BACKEND_URL.
# Trade-off: changing the backend port now requires an image rebuild.
ENV BACKEND_URL=http://127.0.0.1:8081

# F13: Sentry source-map upload token.
#
# Build-time only and never persisted. Declared as an ARG immediately before
# the build so that `--build-arg SENTRY_AUTH_TOKEN` is actually honoured —
# without the ARG, the build arg is accepted on the command line and silently
# discarded, the build succeeds, and the operator believes source maps were
# uploaded when they were not. That failure is invisible: no error, no
# warning, just minified stack traces.
#
# The value is passed to the build as an inline environment variable rather
# than left in the image's ENV, so it does not survive into the runtime layer
# and can be read from the image with `docker inspect`.
ARG SENTRY_AUTH_TOKEN
RUN SENTRY_AUTH_TOKEN="${SENTRY_AUTH_TOKEN}" pnpm exec astro build


FROM python-base AS production
# Node runtime for the Astro standalone server. The production base is
# python:3.14-slim (trixie) which ships no Node; the slice-20 CMD execs
# `node web/concepts/dist/server/entry.mjs` and the container died with
# "exec: node: not found" (exit 127) until this was added. The bookworm
# node binary only needs libstdc++/libgcc/glibc — all satisfied on trixie.
# apt: libstdc++6 explicitly because slim images don't include it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libstdc++6 \
        tini \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
COPY --from=astro-builder /usr/local/bin/node /usr/local/bin/node
EXPOSE 8080
COPY --from=builder-base $PYSETUP_PATH $PYSETUP_PATH
WORKDIR /app
COPY start.sh .
COPY docker/entrypoint.sh ./docker/entrypoint.sh
COPY castor ./castor
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
    chmod +x /app/docker/entrypoint.sh && \
    chown -R nobody /app
USER nobody

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD python healthcheck.py

# DRIFT-1 (audit 2026-09-26): the running image was built from a directory
# that was not a git repository, so there was no reliable way to say which
# commit was deployed — the correspondence was established by hand, by the
# auditor, not by any build-time mechanism. Anything running this image can now
# answer that question directly:
#
#   docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' castor
#
# Defaults to "unknown" so a build that forgets to pass the SHA is visibly
# unlabelled rather than silently mislabelled.
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.source="castor"

# Supervisor: gunicorn (backend, internal :8081) and Astro (frontend, public
# 8080) in one container.
#
# F2: this used to be an inline `sh -c "... & BACKEND_PID=$!; trap ...; exec
# node ..."`. The `exec` replaced the shell, discarding the trap, so
# `docker stop` never signalled gunicorn — it was SIGKILLed by the kernel and
# the pending habit-list writes it held in memory were lost, after the API had
# already acknowledged them. See docker/entrypoint.sh for the full rationale.
#
# tini is PID 1 (`-g` passes signals to the entrypoint's process group). A
# shell as PID 1 cannot reap orphaned children, so a dead worker would
# otherwise linger as a zombie and creep the container's process count up.
ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
CMD ["/app/docker/entrypoint.sh"]
