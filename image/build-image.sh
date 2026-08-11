#!/bin/sh
# Assemble a bootable Ubuntu Core SD card image for the Raspberry Pi.
#
#   BRAND_ID=<account-id> MODEL_KEY=<key-name> \
#     image/build-image.sh path/to/visiontak-client_*_arm64.snap
#
# Must run on arm64 (the gadget is built here, and cross-building it has the same
# problem the client does). Produces image/out/*.img.xz.
#
# The one thing this cannot do for you is create the signing key: model assertions are
# always signed, and the key has to be registered against your Ubuntu One account.
# See docs/sd-image-ci.md.
set -eu

CLIENT_SNAP="${1:-}"
[ -n "$CLIENT_SNAP" ] && [ -f "$CLIENT_SNAP" ] \
  || { echo "usage: image/build-image.sh <visiontak-client_*_arm64.snap>" >&2; exit 2; }

: "${BRAND_ID:?set BRAND_ID to your account-id (snapcraft whoami)}"
: "${MODEL_KEY:?set MODEL_KEY to the registered signing key name}"

GADGET_BRANCH="${GADGET_BRANCH:-24}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
WORK="$HERE/work"
OUT="$HERE/out"

[ "$(dpkg --print-architecture)" = "arm64" ] \
  || { echo "must run on arm64 — see docs/arm64-build.md" >&2; exit 1; }

case ":$PATH:" in *:/snap/bin:*) ;; *) PATH="$PATH:/snap/bin"; export PATH ;; esac

rm -rf "$WORK" "$OUT"
mkdir -p "$WORK" "$OUT"

echo "==> boot splash"
# The gadget wants a 2:1 image; the master is square, so pad rather than stretch —
# a non-uniform scale on a hexagon is obvious on a wall display.
if [ -f "$REPO/assets/visiontak-logo.png" ]; then
  convert "$REPO/assets/visiontak-logo.png" -resize 400x400 -background black \
    -gravity center -extent 800x400 "$WORK/vendor-logo.png"
else
  echo "no assets/visiontak-logo.png — image will keep the stock Ubuntu splash" >&2
fi

echo "==> gadget ($GADGET_BRANCH)"
git clone -q --depth 1 -b "$GADGET_BRANCH" https://github.com/canonical/pi-gadget "$WORK/pi-gadget"

# Ubuntu Core 22+ renders splash/vendor-logo.png from the gadget root. The Pi gadget
# already enables the splash (`splash` and `vt.handoff=2` are in configs/cmdline.txt),
# so supplying the file is the whole change.
if [ -f "$WORK/vendor-logo.png" ]; then
  mkdir -p "$WORK/pi-gadget/splash"
  cp "$WORK/vendor-logo.png" "$WORK/pi-gadget/splash/vendor-logo.png"
fi

# The stock gadget sizes ubuntu-seed for Canonical's own image. This one additionally
# seeds ubuntu-frame, mesa-2404, gnome-46-2404, gtk-common-themes and the client —
# about 1.4 GiB against a 1200M partition. mkfs reports only "Disk full" without
# naming the structure, so the cause is not obvious from the failure.
SEED_SIZE="${SEED_SIZE:-2500M}"
sed -i "s/^\( *\)size: 1200M/\1size: $SEED_SIZE/" "$WORK/pi-gadget/gadget.yaml"
echo "    ubuntu-seed sized to $SEED_SIZE"
grep -n 'size:' "$WORK/pi-gadget/gadget.yaml"

# /dev/cec0 is covered by no built-in interface. Publishing it as a custom-device slot
# keeps CEC an auditable property of the signed image instead of dropping the client to
# classic confinement. Appended only if the gadget does not already declare slots.
if ! grep -q '^slots:' "$WORK/pi-gadget/gadget.yaml"; then
  cat >> "$WORK/pi-gadget/gadget.yaml" <<'EOF'

slots:
  hdmi-cec:
    interface: custom-device
    custom-device: hdmi-cec
    devices:
      - /dev/cec[0-9]
    read-devices:
      - /sys/devices/platform/soc/*.hdmi/cec*
    udev-tagging:
      - kernel: cec[0-9]
        subsystem: cec
EOF
fi

# Destructive mode installs build-packages onto this host, so it needs root. -E keeps
# PATH (and /snap/bin with it) across the sudo.
( cd "$WORK/pi-gadget" && sudo -E snapcraft pack --destructive-mode )
GADGET_SNAP="$(find "$WORK/pi-gadget" -maxdepth 1 -name '*.snap' | head -1)"
[ -n "$GADGET_SNAP" ] || { echo "gadget build produced no snap" >&2; exit 1; }
echo "    gadget: $GADGET_SNAP"

echo "==> model"
STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
sed -e "s|BRAND_ID|$BRAND_ID|g" -e "s|TIMESTAMP|$STAMP|" \
    "$HERE/model.json" > "$WORK/model.json"
echo "    model json written; signing with key '$MODEL_KEY'"
cat "$WORK/model.json"

# snap sign is quiet about *why* it refuses, so keep its stderr rather than letting
# the redirection swallow it with the assertion.
if ! snap sign -k "$MODEL_KEY" "$WORK/model.json" > "$WORK/model.assert" 2> "$WORK/sign.err"; then
  echo "snap sign failed:" >&2
  cat "$WORK/sign.err" >&2
  exit 1
fi
echo "    model signed ($(wc -c < "$WORK/model.assert") bytes)"

echo "==> ubuntu-image"
# grade: dangerous is what allows the locally built, unsigned client and gadget in.
sudo ubuntu-image snap "$WORK/model.assert" -O "$OUT" \
  --snap "$GADGET_SNAP" \
  --snap "$CLIENT_SNAP"
sudo chown -R "$(id -u):$(id -g)" "$OUT"

echo "==> compress"
IMG="$(find "$OUT" -maxdepth 1 -name '*.img' | head -1)"
[ -n "$IMG" ] || { echo "ubuntu-image produced no .img" >&2; exit 1; }
xz -T0 -9 "$IMG"

echo
ls -lh "$OUT"
