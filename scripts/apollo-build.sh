#!/bin/bash
# Start the apollo image build for one commit, detached from SSH.
#   ./apollo-build.sh <tag> <sha>
set -uo pipefail

TAG="${1:?tag required}"
SHA="${2:?sha required}"
LOG=/tmp/deploy-build.log
EXITF=/tmp/deploy-build.exit

cd /opt/castor-build-deploy || exit 1
rm -f "$EXITF" "$LOG"

# setsid detaches from the SSH session's process group so a dropped
# connection cannot kill a 10+ minute build. Without it the build dies
# when ssh exits and the stale log looks like a success.
setsid bash deploy/build-image.sh "$TAG" "$SHA" > "$LOG" 2>&1 < /dev/null &

sleep 15
echo "log: $(stat -c %y "$LOG" 2>/dev/null || echo MISSING)"
echo "--- head ---"
head -4 "$LOG" 2>/dev/null
echo "--- running? ---"
pgrep -af 'docker buildx|build-image.sh' | head -3 || echo "NOT RUNNING"
