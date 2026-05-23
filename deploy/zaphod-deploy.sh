#!/usr/bin/env bash
# zaphod-deploy.sh — push local changes to zaphod and rebuild the container.
#
# Designed to be idempotent + safe against config loss:
#   - --exclude=.env preserves deploy-only secrets across rsyncs
#   - --exclude=node_modules / .venv / .git / dist keeps the transfer small
#   - delete-after happens BEFORE we restart, so a failed transfer doesn't
#     leave the container running against half-synced source
#
# Usage:
#   ./deploy/zaphod-deploy.sh              # sync + build + restart
#   ./deploy/zaphod-deploy.sh --no-build   # sync only (for hot-reload-friendly fixes)
#   ./deploy/zaphod-deploy.sh --no-restart # build but leave the running container

set -euo pipefail

HOST="${ZAPHOD_HOST:-zaphod-beeblebox@192.168.6.56}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_DIR="${ZAPHOD_REMOTE_DIR:-/home/zaphod-beeblebox/src/uap-analyzer}"

DO_BUILD=1
DO_RESTART=1
for arg in "$@"; do
    case "$arg" in
        --no-build) DO_BUILD=0 ;;
        --no-restart) DO_RESTART=0 ;;
        -h|--help)
            sed -n '2,/^set/ p' "$0" | sed '$d'
            exit 0
            ;;
        *) echo "unknown arg: $arg" >&2 ; exit 2 ;;
    esac
done

echo "==> rsync ${REPO_DIR}/ -> ${HOST}:${REMOTE_DIR}/"
rsync -aP --delete \
    --exclude='.env' \
    --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='.venv' --exclude='venv' \
    --exclude='node_modules' --exclude='.git' \
    --exclude='.pytest_cache' --exclude='.mypy_cache' --exclude='.ruff_cache' \
    --exclude='dist' --exclude='build' --exclude='*.egg-info' \
    --exclude='site/dist' --exclude='site/.astro' \
    "${REPO_DIR}/" "${HOST}:${REMOTE_DIR}/"

if [[ "$DO_BUILD" == "1" ]]; then
    echo "==> docker compose build on ${HOST}"
    ssh "${HOST}" "cd ${REMOTE_DIR} && docker compose build"
fi

if [[ "$DO_RESTART" == "1" ]]; then
    echo "==> docker compose up -d on ${HOST}"
    ssh "${HOST}" "cd ${REMOTE_DIR} && docker compose up -d"
    sleep 3
    echo "==> healthz"
    curl -fsS "http://${HOST#*@}:3260/healthz" | python3 -m json.tool
fi

echo "==> done"
