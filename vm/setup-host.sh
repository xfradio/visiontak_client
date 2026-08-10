#!/bin/sh
# Provision the Linux build host that runs the Ubuntu Core test VM.
#
# Run this inside an Ubuntu 24.04 machine — the same release as the snap's `core24`
# base, so `--destructive-mode` builds see exactly the libraries snapcraft would
# stage in a clean LXD container. On Windows that machine is a WSL2 distro:
#
#   wsl --install -d Ubuntu-24.04 --no-launch
#   wsl -d Ubuntu-24.04 -u root -- /mnt/c/…/vm/setup-host.sh
#
# Idempotent: safe to re-run after a distro reset.
set -eu

STATE_DIR="${VM_STATE_DIR:-/var/lib/visiontak-vm}"
IMAGE_URL="https://cdimage.ubuntu.com/ubuntu-core/24/stable/current/ubuntu-core-24-amd64.img.xz"
IMAGE_XZ="$STATE_DIR/ubuntu-core-24-amd64.img.xz"
IMAGE="$STATE_DIR/ubuntu-core-24-amd64.img"

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }

. /etc/os-release
[ "${VERSION_ID:-}" = "24.04" ] || cat >&2 <<EOF
warning: this host is ${PRETTY_NAME:-unknown}, not Ubuntu 24.04.
         --destructive-mode builds will link against the wrong libraries.
EOF

# systemd is required for snapd, and snapd is required for snapcraft. WSL only
# starts it when asked.
if [ -d /run/WSL ] && ! grep -qs '^systemd=true' /etc/wsl.conf; then
  cat > /etc/wsl.conf <<'EOF'
[boot]
systemd=true

[automount]
options = "metadata"

[interop]
appendWindowsPath = false
EOF
  echo "wrote /etc/wsl.conf — run 'wsl --terminate <distro>' and re-run this script"
  exit 0
fi

echo "==> packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  qemu-system-x86 qemu-system-gui qemu-utils ovmf \
  xz-utils wget curl ca-certificates openssh-client rsync \
  snapd netcat-openbsd python3 make git

echo "==> snapcraft"
if ! snap list snapcraft >/dev/null 2>&1; then
  snap install snapcraft --classic
fi

echo "==> ssh key"
# Dedicated to the test guest. console-conf can only import a public key that is
# already on an Ubuntu One account, so this .pub has to be uploaded once to
# https://login.ubuntu.com/ssh-keys before first boot.
mkdir -p "$STATE_DIR"
if [ ! -f "$STATE_DIR/ssh_key" ]; then
  ssh-keygen -t ed25519 -N '' -C visiontak-vm -f "$STATE_DIR/ssh_key" >/dev/null
fi
chmod 0600 "$STATE_DIR/ssh_key"

echo "==> ubuntu core image"
mkdir -p "$STATE_DIR"
if [ ! -f "$IMAGE" ]; then
  [ -f "$IMAGE_XZ" ] || wget -q --show-progress -O "$IMAGE_XZ" "$IMAGE_URL"
  # Keep the compressed original: `vm/run.sh --reset` only discards the overlay,
  # but a corrupted base image should be recoverable without a 450 MB download.
  xz -dk -T0 -f "$IMAGE_XZ"
fi

echo
echo "host ready. image: $IMAGE"
echo
echo "upload this key to https://login.ubuntu.com/ssh-keys before first boot:"
cat "$STATE_DIR/ssh_key.pub"
echo
if [ -e /dev/kvm ]; then
  echo "kvm:   available (hardware accelerated)"
else
  echo "kvm:   MISSING — the VM will fall back to TCG emulation and crawl."
  echo "       On WSL add to %USERPROFILE%\\.wslconfig:"
  echo "         [wsl2]"
  echo "         nestedVirtualization=true"
  echo "       then 'wsl --shutdown'."
fi
