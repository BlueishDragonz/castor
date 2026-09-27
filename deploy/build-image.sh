#!/bin/bash
# Build the Castör production image on the build host.
#
# Runs detached (setsid) so it survives the SSH session that started it, and
# records its exit code in a separate file so a half-finished build can never
# be mistaken for a successful one.
#
# Usage: ./build-image.sh <tag> <git-sha>
#   SENTRY_TOKEN_SRC  path to a file holding the org auth token (default
#                     /tmp/sentry_token). Absent => build still works, but
#                     source maps cannot be uploaded and it says so.
#
# Why the frontend stage is never cached when a token is present: see the
# comment on --no-cache-filter below. A cached build step that performs the
# upload reports success while uploading nothing.
#
# `nohup cmd &` alone is not enough over SSH: the remote shell's process group
# is signalled when the connection drops. setsid puts the build in a new session
# with no controlling terminal, so a closed connection does not reach it.
#
# The exit code is written to a separate file, not appended to the build log,
# because a log tail is ambiguous while a build is still running.
#
# The astro build stage MUST NOT be served from Docker's layer cache when a
# Sentry token is supplied. The first run with a valid token came back "exit 0"
# with no upload and no error, because the stage was reported CACHED: the
# `astro build` step never executed, so the token was never read and no source
# maps reached Sentry. A silent no-op is worse than a visible failure, because
# the build log looks exactly like a success.
set -uo pipefail

BUILD_DIR="${BUILD_DIR:-/opt/castor-build-deploy}"
TAG="${1:-castor:deploy-20260926}"
SHA="${2:-unknown}"
TOKEN_SRC="${SENTRY_TOKEN_SRC:-/tmp/sentry_token}"

cat > /tmp/deploy-build-inner.sh <<INNER
#!/bin/bash
cd ${BUILD_DIR}
rm -f /tmp/deploy-build.exit

SECRET_ARGS=""
FORCE=""
if [ -f "${TOKEN_SRC}" ]; then
    SECRET_ARGS="--secret id=sentry_token,src=${TOKEN_SRC}"
    FORCE="--no-cache-filter astro-builder"
    echo "source-map upload: ENABLED (frontend stage will not be cached)"
else
    # No token: the build still works, it just cannot upload source maps.
    # Said out loud, because a silent skip is indistinguishable from success.
    echo "WARNING: no Sentry token at ${TOKEN_SRC}."
    echo "WARNING: build will succeed with NO source maps uploaded."
fi

docker build \\
    \${SECRET_ARGS} \\
    \${FORCE} \\
    --build-arg GIT_SHA="${SHA}" \\
    -f docker/Dockerfile \\
    -t "${TAG}" \\
    . > /tmp/deploy-build.log 2>&1
rc=\$?
echo "\$rc" > /tmp/deploy-build.exit
exit \$rc
INNER
chmod +x /tmp/deploy-build-inner.sh

: > /tmp/deploy-build.log
rm -f /tmp/deploy-build.exit
setsid /tmp/deploy-build-inner.sh < /dev/null > /dev/null 2>&1 &
disown

sleep 4
# Check for the inner script itself, not a pattern that also matches the
# checking command. `pgrep -f "docker build"` matches the ssh command line
# running pgrep, which reports a dead build as running.
if pgrep -x -f "/bin/bash /tmp/deploy-build-inner.sh" > /dev/null 2>&1 \
   || pgrep -f "deploy-build-inner.sh" | grep -qv "^$$\$"; then
    echo "BUILD STARTED (detached, tag ${TAG})"
else
    echo "build process not found — check /tmp/deploy-build.log"
fi
