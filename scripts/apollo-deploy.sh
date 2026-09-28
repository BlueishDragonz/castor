#!/bin/bash
# Cut production over to a new image tag, with a compose backup first.
#   ./apollo-deploy.sh <tag>
set -uo pipefail

TAG="${1:?tag required}"
COMPOSE=/opt/castor/docker-compose.yml
STAMP=$(date +%Y%m%d-%H%M%S)

# Prune only dangling images; tagged rollback images must survive.
docker image prune -f >/dev/null
echo "disk after prune: $(df -h / | tail -1 | awk '{print $5}')"

cp "$COMPOSE" "$COMPOSE.pre-$STAMP"
echo "compose backed up to $COMPOSE.pre-$STAMP"

sed -i "s|image: castor:[^ ]*|image: $TAG|" "$COMPOSE"
echo "compose now: $(grep -m1 'image: castor:' "$COMPOSE" | tr -s ' ')"

cd /opt/castor || exit 1
docker compose up -d 2>&1 | tail -3
