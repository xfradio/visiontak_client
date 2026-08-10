#!/bin/sh
# Build the snap and install it into the running Ubuntu Core VM.
#
#   VM_USER=rob vm/deploy.sh
#   VM_USER=rob vm/deploy.sh --skip-build          # reuse the last .snap
#   VM_USER=rob SERVER_URL=http://10.0.2.2:8080 API_TOKEN=… vm/deploy.sh
#
# VM_USER is the Ubuntu SSO name console-conf created on first boot.
set -eu

# Snaps live in /snap/bin, which only login shells add to PATH — so a non-interactive
# `sh vm/deploy.sh` from cron or CI would otherwise fail with "snapcraft: not found".
case ":$PATH:" in *:/snap/bin:*) ;; *) PATH="$PATH:/snap/bin"; export PATH ;; esac

REPO="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="${VM_STATE_DIR:-/var/lib/visiontak-vm}"
BUILD_DIR="${VM_BUILD_DIR:-$STATE_DIR/build}"
SSH_PORT="${VM_SSH_PORT:-8022}"
SKIP_BUILD=0

[ "${1:-}" = "--skip-build" ] && SKIP_BUILD=1
: "${VM_USER:?set VM_USER to the account console-conf created on first boot}"

# console-conf imports the *public* half from the Ubuntu One account, so the matching
# private key has to exist on this host. setup-host.sh generates one next to the rest
# of the VM state rather than reusing a personal key, which keeps a throwaway test
# guest off whatever key you use for real machines.
KEY="${VM_SSH_KEY:-$STATE_DIR/ssh_key}"
[ -f "$KEY" ] || { echo "no private key at $KEY — run vm/setup-host.sh" >&2; exit 1; }

SSH_OPTS="-p $SSH_PORT -i $KEY -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$STATE_DIR/known_hosts"
SSH="ssh $SSH_OPTS"
TARGET="$VM_USER@localhost"

if [ "$SKIP_BUILD" -eq 0 ]; then
  echo "==> build"
  # Building straight out of /mnt/c is roughly an order of magnitude slower —
  # snapcraft is metadata-heavy and drvfs makes every stat a round trip. Mirror the
  # tree onto ext4 first. Excludes keep stale artefacts from the Windows side out.
  mkdir -p "$BUILD_DIR"
  rsync -a --delete \
    --exclude '.git/' --exclude '.venv/' --exclude '__pycache__/' \
    --exclude 'parts/' --exclude 'stage/' --exclude 'prime/' \
    --exclude '.local-data/' \
    "$REPO/" "$BUILD_DIR/"
  # Destructive mode installs build-packages onto this host rather than into an LXD
  # container. That is the point of a disposable build VM, and it keeps us off
  # nested LXD inside WSL. The host is Ubuntu 24.04, which *is* the core24 base.
  ( cd "$BUILD_DIR" && snapcraft pack --destructive-mode )
fi

SNAP="$(ls -t "$BUILD_DIR"/visiontak-client_*_amd64.snap 2>/dev/null | head -1)"
[ -n "$SNAP" ] || { echo "no .snap in $BUILD_DIR" >&2; exit 1; }
echo "==> $(basename "$SNAP")"

nc -z localhost "$SSH_PORT" 2>/dev/null || {
  echo "nothing listening on $SSH_PORT — is vm/run.sh up?" >&2; exit 1; }

# The stock image's writable partition is ~1.7 GiB and a pre-installed Core image
# boots straight into run mode, so snap-bootstrap never expands it. Without this the
# install dies partway through with "cannot communicate with server: ... EOF" — snapd
# hitting ENOSPC. growpart is a no-op once the partition already fills the disk.
echo "==> disk"
$SSH "$TARGET" 'sudo growpart /dev/vda 5 || true
sudo resize2fs /dev/vda5 || true
df -h /writable | tail -1'

echo "==> ubuntu-frame"
# The compositor owns the display; without it the client exits on "no Wayland
# compositor". idle-timeout=0 because a wall display must never blank itself.
$SSH "$TARGET" 'sudo snap list ubuntu-frame >/dev/null 2>&1 || sudo snap install ubuntu-frame
sudo snap set ubuntu-frame daemon=true cursor=none idle-timeout=0'

echo "==> install"
# scp spells the port -P where ssh spells it -p; everything else is shared.
scp $(echo "$SSH_OPTS" | sed 's/^-p /-P /') "$SNAP" "$TARGET:/tmp/"
$SSH "$TARGET" "sudo snap install --dangerous /tmp/$(basename "$SNAP")"

# There is no /dev/cec0 in a VM and no gadget to provide the hdmi-cec slot, so the
# CEC backend has to be switched off explicitly — 'auto' would log a failure every
# start. Everything else about the run is the real thing.
CONF="cec-backend=none"
[ -n "${SERVER_URL:-}" ] && CONF="$CONF server-url=$SERVER_URL"
[ -n "${API_TOKEN:-}" ] && CONF="$CONF api-token=$API_TOKEN"
[ -n "${DEVICE_ID:-}" ] && CONF="$CONF device-id=$DEVICE_ID"

echo "==> configure: $(echo "$CONF" | sed 's/api-token=[^ ]*/api-token=***/')"
$SSH "$TARGET" "sudo snap set visiontak-client $CONF"

# On a --dangerous install snapd auto-connected our custom-device plug to
# console-conf:terminal-devices, whose custom-device attribute is "terminal-control",
# not "hdmi-cec". That is not CEC — it writes /dev/tty[0-9] and /dev/ttyS[0-9] rwk
# straight into the apparmor profile. Drop it so the VM reflects the confinement the
# image is supposed to have; on a real device hdmi-cec must come from the gadget.
echo "==> confinement"
$SSH "$TARGET" '
slot=$(snap connections visiontak-client | awk "\$2 == \"visiontak-client:hdmi-cec\" {print \$3}")
case "$slot" in
  ""|"-") echo "hdmi-cec: unconnected (expected without a gadget slot)" ;;
  *hdmi-cec) echo "hdmi-cec: connected to $slot" ;;
  *) echo "hdmi-cec: WRONG SLOT $slot — disconnecting"
     sudo snap disconnect visiontak-client:hdmi-cec ;;
esac'

echo "==> connections"
$SSH "$TARGET" 'snap connections visiontak-client'

echo
if [ "${VM_FOLLOW:-1}" = "1" ]; then
  echo "==> logs (Ctrl-C to stop)"
  $SSH "$TARGET" 'sudo snap logs -f visiontak-client'
else
  # Bounded, so the script can be driven from CI or a non-interactive shell.
  echo "==> logs (last 50)"
  $SSH "$TARGET" 'sudo snap logs -n 50 visiontak-client'
fi
