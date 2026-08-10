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

( cd "$WORK/pi-gadget" && snapcraft pack --destructive-mode --output "$WORK/pi-gadget.snap" )

echo "==> model"
sed -e "s/BRAND_ID/$BRAND_ID/g" \
    -e "s/TIMESTAMP/$(date -u +%Y-%m-%dT%H:%M:%SZ)/" \
    "$HERE/model.json" > "$WORK/model.json"
snap sign -k "$MODEL_KEY" "$WORK/model.json" > "$WORK/model.assert"

echo "==> ubuntu-image"
# grade: dangerous is what allows the locally built, unsigned client and gadget in.
ubuntu-image snap "$WORK/model.assert" -O "$OUT" \
  --snap "$WORK/pi-gadget.snap" \
  --snap "$CLIENT_SNAP"

echo "==> compress"
IMG="$(find "$OUT" -maxdepth 1 -name '*.img' | head -1)"
[ -n "$IMG" ] || { echo "ubuntu-image produced no .img" >&2; exit 1; }
xz -T0 -9 "$IMG"

echo
ls -lh "$OUT"
