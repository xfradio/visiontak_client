#!/bin/sh
# Build the arm64 snap. Runs ON an arm64 Ubuntu 24.04 host — a cloud VM, or a Pi 4/5
# running Ubuntu Server. Not on your workstation: see docs/arm64-build.md for why an
# x86 box cannot produce this artifact.
#
#   sh vm/build-arm64.sh
#
# Idempotent, and safe to re-run after a failed build.
set -eu

# snapcraft installs to /snap/bin, which only login shells pick up.
case ":$PATH:" in *:/snap/bin:*) ;; *) PATH="$PATH:/snap/bin"; export PATH ;; esac

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

ARCH="$(dpkg --print-architecture)"
[ "$ARCH" = "arm64" ] || {
  echo "this host is $ARCH, not arm64." >&2
  echo "The gnome extension needs arch-matched build-snaps, so cross-building is not" >&2
  echo "possible — see docs/arm64-build.md." >&2
  exit 1
}

. /etc/os-release
[ "${VERSION_ID:-}" = "24.04" ] || cat >&2 <<EOF
warning: this host is ${PRETTY_NAME:-unknown}, not Ubuntu 24.04.
         --destructive-mode builds against the host, and 24.04 *is* the core24 base,
         so anything else links against the wrong libraries.
EOF

echo "==> packages"
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq --no-install-recommends snapd git rsync

echo "==> snapcraft"
snap list snapcraft >/dev/null 2>&1 || sudo snap install snapcraft --classic

echo "==> free space"
# Staging WebKit and the gnome SDK is the disk hog, not the source tree.
avail=$(df -Pm . | awk 'NR==2 {print $4}')
[ "$avail" -ge 12000 ] || echo "warning: only ${avail} MiB free; the build wants ~12 GiB" >&2

echo "==> build"
# Destructive mode rather than LXD: a disposable build host is exactly what this is
# for, and it avoids nesting containers inside a cloud VM.
sudo -E snapcraft pack --destructive-mode

echo
ls -1 "$REPO"/visiontak-client_*_arm64.snap 2>/dev/null || {
  echo "build reported success but produced no arm64 snap" >&2
  exit 1
}
