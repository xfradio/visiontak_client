#!/bin/sh
# Drive an arm64 build host from this workstation: push the tree, build, pull the snap.
#
#   vm/remote-build.sh ubuntu@1.2.3.4
#   BUILD_SSH_KEY=~/.ssh/oracle vm/remote-build.sh ubuntu@1.2.3.4
#
# The host must be arm64 Ubuntu 24.04 — a cloud VM or a Pi 4/5. See
# docs/arm64-build.md.
set -eu

TARGET="${1:-}"
[ -n "$TARGET" ] || { echo "usage: vm/remote-build.sh <user@host>" >&2; exit 2; }

REPO="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_DIR="${BUILD_REMOTE_DIR:-~/visiontak-client}"
OUT="${BUILD_OUT_DIR:-$REPO/dist}"

SSH="ssh -o StrictHostKeyChecking=accept-new"
[ -n "${BUILD_SSH_KEY:-}" ] && SSH="$SSH -i $BUILD_SSH_KEY -o IdentitiesOnly=yes"

echo "==> push"
# Excludes matter: parts/ and stage/ from an amd64 build would poison an arm64 one,
# and .venv holds x86 binaries.
rsync -az --delete -e "$SSH" \
  --exclude '.git/' --exclude '.venv/' --exclude '__pycache__/' \
  --exclude 'parts/' --exclude 'stage/' --exclude 'prime/' \
  --exclude 'dist/' --exclude '*.snap' --exclude '.local-data/' \
  "$REPO/" "$TARGET:$REMOTE_DIR/"

echo "==> build (this is the slow part)"
$SSH "$TARGET" "cd $REMOTE_DIR && sh vm/build-arm64.sh"

echo "==> fetch"
mkdir -p "$OUT"
rsync -az -e "$SSH" "$TARGET:$REMOTE_DIR/visiontak-client_*_arm64.snap" "$OUT/"

echo
ls -lh "$OUT"/visiontak-client_*_arm64.snap
echo
echo "install it on the Pi with:"
echo "  scp $OUT/visiontak-client_*_arm64.snap <user>@<pi>:/tmp/"
echo "  ssh <user>@<pi> 'sudo snap install --dangerous /tmp/visiontak-client_*_arm64.snap'"
