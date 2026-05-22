#!/usr/bin/env bash
# One-shot deploy of uap-analyzer to zaphod (192.168.6.56).
#
# Idempotent: re-runs rsync the source + corpus, rebuilds the image, restarts.
#
# Usage:
#   ./deploy/zaphod-bootstrap.sh [--skip-corpus]
#
#   --skip-corpus   Skip rsyncing the corpus (videos + Release_1.zip). Use after
#                   the first run when only the code has changed.

set -euo pipefail

ZAPHOD_HOST="${ZAPHOD_HOST:-192.168.6.56}"
ZAPHOD_USER="${ZAPHOD_USER:-zaphod-beeblebox}"
ZAPHOD_SSH_PORT="${ZAPHOD_SSH_PORT:-2222}"
REMOTE_BASE="${REMOTE_BASE:-/home/${ZAPHOD_USER}/uap-analyzer}"
REMOTE_DATA="${REMOTE_DATA:-/home/${ZAPHOD_USER}/uap-data}"

LOCAL_REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOCAL_VIDEOS="${LOCAL_VIDEOS:-${HOME}/Downloads/uapvideos}"
LOCAL_RELEASE_ZIP="${LOCAL_RELEASE_ZIP:-${HOME}/Downloads/uapvideos/Release_1.zip}"

skip_corpus=0
for arg in "$@"; do
  case "$arg" in
    --skip-corpus) skip_corpus=1 ;;
    *) echo "unknown arg: $arg"; exit 2 ;;
  esac
done

ssh_remote() {
  ssh -p "${ZAPHOD_SSH_PORT}" -o StrictHostKeyChecking=accept-new \
    "${ZAPHOD_USER}@${ZAPHOD_HOST}" "$@"
}

rsync_remote() {
  rsync -e "ssh -p ${ZAPHOD_SSH_PORT}" "$@"
}

echo "==> 1/5 prepare remote dirs (no sudo — using user-owned path)"
ssh_remote "mkdir -p ${REMOTE_DATA}/videos ${REMOTE_DATA}/Release_1 ${REMOTE_DATA}/.cache ${REMOTE_BASE}"

if (( skip_corpus == 0 )); then
  echo "==> 2/5 rsync corpus videos (${LOCAL_VIDEOS} → ${REMOTE_DATA}/videos)"
  rsync_remote -avP --include='*.mp4' --include='*.mov' --include='*.mkv' \
    --exclude='thumbs/' --exclude='contact-sheet.jpg' --exclude='Release_1.zip' --exclude='*' \
    "${LOCAL_VIDEOS}/" "${ZAPHOD_USER}@${ZAPHOD_HOST}:${REMOTE_DATA}/videos/"

  if [[ -f "${LOCAL_RELEASE_ZIP}" ]]; then
    echo "==> 3/5 rsync + unpack Release_1.zip"
    rsync_remote -avP "${LOCAL_RELEASE_ZIP}" "${ZAPHOD_USER}@${ZAPHOD_HOST}:${REMOTE_DATA}/Release_1.zip"
    ssh_remote "cd ${REMOTE_DATA} && unzip -o -q Release_1.zip -d . && rm -rf __MACOSX && rm -f Release_1.zip"
  else
    echo "    (skipping Release_1.zip — not found at ${LOCAL_RELEASE_ZIP})"
  fi
else
  echo "==> 2-3/5 skipping corpus rsync (--skip-corpus)"
fi

echo "==> 4/5 rsync source repo (${LOCAL_REPO} → ${REMOTE_BASE})"
rsync_remote -avP --delete \
  --exclude='.git/' --exclude='.venv/' --exclude='__pycache__/' \
  --exclude='*.egg-info/' --exclude='.pytest_cache/' --exclude='dist/' --exclude='build/' \
  "${LOCAL_REPO}/" "${ZAPHOD_USER}@${ZAPHOD_HOST}:${REMOTE_BASE}/"

echo "==> 5/5 build + restart container on zaphod"
ssh_remote "cd ${REMOTE_BASE} && UAP_HOST_DATA_DIR=${REMOTE_DATA} docker compose up -d --build"

echo
echo "Deployed. Verify:"
echo "  curl -sS http://${ZAPHOD_HOST}:3260/healthz | jq"
echo "  claude mcp add --transport http uap-analyzer http://${ZAPHOD_HOST}:3260/mcp"
