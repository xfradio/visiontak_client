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
  # Keep the mark well inside the canvas. Televisions overscan HDMI by a few percent
  # and plymouth scales this to the panel, so a logo filling its canvas gets clipped
  # at the edges on real hardware — which is exactly what happened on first boot.
  convert "$REPO/assets/visiontak-logo.png" -resize 300x300 -background black \
    -gravity center -extent 800x400 "$WORK/vendor-logo.png"
else
  echo "no assets/visiontak-logo.png — image will keep the stock Ubuntu splash" >&2
fi

echo "==> gadget ($GADGET_BRANCH)"
git clone -q --depth 1 -b "$GADGET_BRANCH" https://github.com/canonical/pi-gadget "$WORK/pi-gadget"

# Ubuntu Core 22+ renders splash/vendor-logo.png from the gadget root. The Pi gadget
# already enables the splash (`splash` and `vt.handoff=2` are in configs/cmdline.txt),
# so supplying the file is the whole change.
# Both customisations ride into the gadget the same way. Dropping files in the cloned
# tree is not enough: the gadget has a single part with `source: configs`, and
# snapcraft only auto-includes gadget.yaml — so anything else at the project root is
# never staged, and the build succeeds while shipping none of it.
EXTRAS="$WORK/pi-gadget/vt-extras"
mkdir -p "$EXTRAS"
ORGANIZE=""

if [ -f "$WORK/vendor-logo.png" ]; then
  # Ubuntu Core 22+ renders splash/vendor-logo.png from the gadget root, and the Pi
  # gadget already enables the splash (`splash` and `vt.handoff=2` are in
  # configs/cmdline.txt), so supplying the file is the whole change.
  cp "$WORK/vendor-logo.png" "$EXTRAS/vendor-logo.png"
  ORGANIZE="$ORGANIZE      vendor-logo.png: splash/vendor-logo.png\n"
fi

# cloud.conf is NOT shipped. It is the documented UC20+ route for cloud-init config
# and it does reach the device — snapd installs it as
# /etc/cloud/cloud.cfg.d/80_device_gadget.cfg — but it never executes: Ubuntu Core
# writes /etc/cloud/cloud-init.disabled when a device seeds without a datasource, so
# cloud-init is disabled before any module runs. Verified on hardware.
#
# image/cloud-init.yaml is kept for whenever this is revisited. Shipping it as-is only
# adds a file that looks like it does something. See docs/dhcp-discovery.md.

if [ -n "$ORGANIZE" ]; then
  {
    printf '  vt-extras:\n    plugin: dump\n    source: vt-extras\n    organize:\n'
    printf '%b' "$ORGANIZE"
    printf '\n'
  } > "$WORK/extras-part.yaml"
  awk -v partfile="$WORK/extras-part.yaml" '
    /^slots:/ && !ins {
      while ((getline line < partfile) > 0) print line
      ins = 1
    }
    { print }
    END { if (!ins) exit 3 }
  ' "$WORK/pi-gadget/snapcraft.yaml" > "$WORK/snapcraft.yaml.new" \
    || { echo "could not find a slots: stanza to insert the extras part before" >&2; exit 1; }
  mv "$WORK/snapcraft.yaml.new" "$WORK/pi-gadget/snapcraft.yaml"
fi

# /dev/cec0 is covered by no built-in interface. A custom-device slot keeps CEC an
# auditable property of the image, visible in `snap connections` and revocable with
# `snap disconnect`, rather than a reason to drop the client to classic confinement.
#
# Declared here rather than in gadget.yaml: snap slots live in snapcraft.yaml, which
# becomes meta/snap.yaml. gadget.yaml accepts a slots block and ignores it.
if ! grep -q '^  hdmi-cec:' "$WORK/pi-gadget/snapcraft.yaml"; then
  awk '
    /^slots:/ && !ins {
      print
      print "  hdmi-cec:"
      print "    interface: custom-device"
      print "    custom-device: hdmi-cec"
      print "    devices:"
      print "      - /dev/cec0"
      print "      - /dev/cec1"
      print "    udev-tagging:"
      print "      - kernel: cec[0-9]"
      print "        subsystem: cec"
      ins = 1
      next
    }
    { print }
    END { if (!ins) exit 3 }
  ' "$WORK/pi-gadget/snapcraft.yaml" > "$WORK/snapcraft.yaml.slots" \
    || { echo "no slots: stanza in the gadget snapcraft.yaml to extend" >&2; exit 1; }
  mv "$WORK/snapcraft.yaml.slots" "$WORK/pi-gadget/snapcraft.yaml"
  echo "    hdmi-cec slot declared in the gadget"
fi

# The stock gadget sizes ubuntu-seed for Canonical's own image. This one additionally
# seeds ubuntu-frame, mesa-2404, gnome-46-2404, gtk-common-themes and the client —
# about 1.4 GiB against a 1200M partition. mkfs reports only "Disk full" without
# naming the structure, so the cause is not obvious from the failure.
# The stock gadget boots with fake KMS, where the firmware owns the display and no
# Linux CEC adapter is ever registered — /dev/cec0 simply does not exist, and dmesg
# mentions no cec at all. Full KMS is what exposes the vc4 CEC adapter, which is the
# entire basis of remote control here.
# kms | fkms. Full KMS is the default because it is the only way /dev/cec0 exists —
# under fake KMS the firmware owns the display and no CEC adapter is ever registered.
# DISPLAY_DRIVER=fkms reverts, at the cost of the remote.
DISPLAY_DRIVER="${DISPLAY_DRIVER:-kms}"
CMDLINE="$WORK/pi-gadget/configs/cmdline.txt"

if [ "$DISPLAY_DRIVER" = "kms" ]; then
  sed -i 's/vc4-fkms-v3d/vc4-kms-v3d/' "$WORK/pi-gadget/configs/config.txt"

  # vt.handoff=2 defers the console to a firmware framebuffer, which full KMS never
  # creates — the handoff has no owner and the text console stays on screen instead
  # of the splash. It belongs with fake KMS, so it moves with it.
  sed -i 's/ *vt\.handoff=2//' "$CMDLINE"

  # Two console= entries are listed, one of them serial. Without this plymouth can
  # take the serial console for its output and draw nothing on HDMI.
  grep -q 'plymouth.ignore-serial-consoles' "$CMDLINE" \
    || sed -i 's/$/ plymouth.ignore-serial-consoles/' "$CMDLINE"

  # Full KMS relies on the kernel detecting the panel, where fake KMS inherited the
  # firmware's setup. A display behind a switch, a long cable, or one that is not
  # awake when the Pi boots can leave a Pi 4 with no output at all. Forcing hotplug
  # makes it drive HDMI regardless of what it reads back.
  if ! grep -q 'hdmi_force_hotplug' "$WORK/pi-gadget/configs/config.txt"; then
    printf '\n[pi4]\nhdmi_force_hotplug=1\n[all]\n' >> "$WORK/pi-gadget/configs/config.txt"
    echo "    hdmi_force_hotplug=1 set for pi4"
  fi

  echo "    full KMS (vc4-kms-v3d); vt.handoff dropped so plymouth owns the console"
else
  echo "    fake KMS retained — /dev/cec0 will not exist and the remote will not work" >&2
fi
echo "    cmdline: $(cat "$CMDLINE")"

# The gadget fixes CMA at 128 MB for every model. That is right for a Pi 3 B+, where
# 256 would be a quarter of the board's entire memory, and mean for a Pi 4 driving a
# large panel with video. Raise it per-build rather than per-model: config.txt filters
# would need one dtoverlay line per model, and a model nobody listed would then get no
# display at all — a worse failure than a conservative default.
CMA_MB="${CMA_MB:-128}"
sed -i "s/\(dtoverlay=vc4-kms-v3d\),cma-[0-9]*/\1,cma-$CMA_MB/" \
  "$WORK/pi-gadget/configs/config.txt"
echo "    CMA set to ${CMA_MB}M (CMA_MB= to change; 256 suits a Pi 4)"
grep -n 'dtoverlay=vc4' "$WORK/pi-gadget/configs/config.txt" || true

SEED_SIZE="${SEED_SIZE:-2500M}"
sed -i "s/^\( *\)size: 1200M/\1size: $SEED_SIZE/" "$WORK/pi-gadget/gadget.yaml"
echo "    ubuntu-seed sized to $SEED_SIZE"
grep -n 'size:' "$WORK/pi-gadget/gadget.yaml"

# Boot straight into the kiosk. Otherwise first boot stops at console-conf waiting for
# a keyboard, which is exactly what a wall display does not have — and the client's own
# setup screen asks for the one thing that genuinely cannot be defaulted, the server
# address, on screen instead.
#
# Chosen over a signed system-user assertion deliberately: that route needs a password
# hash or SSH key baked into the image, and this repository is public. Disabling
# console-conf needs no credential at all. The trade-off is that the device has no
# login; add a system-user assertion if you need SSH access to a field unit.
#
# Ubuntu Frame's daemon is OFF by default: installing it is not the same as running
# it. Without this the image boots with no compositor at all, the client cannot get a
# Wayland surface, and tty1 shows a bare login prompt instead of the kiosk. Defaults
# are keyed by snap-id, hence the opaque key — it is ubuntu-frame.
if ! grep -q '^defaults:' "$WORK/pi-gadget/gadget.yaml"; then
  cat >> "$WORK/pi-gadget/gadget.yaml" <<'EOF'

defaults:
  system:
    console-conf:
      disable: true
  # ubuntu-frame
  BPZbvWzvoMTrpec4goCXlckLe2IhfthK:
    daemon: true
    cursor: none
    idle-timeout: 0
EOF
  echo "    console-conf disabled, ubuntu-frame set to run as a daemon"
fi

# /dev/cec0 is covered by no built-in interface. Publishing it as a custom-device slot
# keeps CEC an auditable property of the signed image instead of dropping the client to
# classic confinement. Appended only if the gadget does not already declare slots.
# Only `connections` goes in gadget.yaml. The slot itself is declared in the gadget's
# snapcraft.yaml, alongside its GPIO/I2C/SPI slots — see below. Putting a `slots:`
# block here parses without complaint and is then ignored, which is how the gadget
# shipped with no hdmi-cec slot at all ("snap \"pi\" has no slot named \"hdmi-cec\"").
#
# snapd will not auto-connect custom-device on its own: it needs a store
# snap-declaration or this. Without it the plug sits unconnected, /dev/cec0 is
# unreachable and the client falls back to NullCecBackend.
#
# The slot reference is deliberately omitted — snapd reads that as the gadget's own
# slot of the same name. Naming it fails to parse ("expected (<snap-id>|system):name")
# and a locally built gadget has no snap-id to give.
if ! grep -q '^connections:' "$WORK/pi-gadget/gadget.yaml"; then
  cat >> "$WORK/pi-gadget/gadget.yaml" <<'EOF'

connections:
  - plug: visiontak-client:hdmi-cec
EOF
fi

# Destructive mode installs build-packages onto this host, so it needs root. -E keeps
# PATH (and /snap/bin with it) across the sudo.
( cd "$WORK/pi-gadget" && sudo -E snapcraft pack --destructive-mode )
GADGET_SNAP="$(find "$WORK/pi-gadget" -maxdepth 1 -name '*.snap' | head -1)"
[ -n "$GADGET_SNAP" ] || { echo "gadget build produced no snap" >&2; exit 1; }
echo "    gadget: $GADGET_SNAP"

# Assert the logo is actually inside the snap. A gadget that builds cleanly without it
# produces an image that boots to the stock Ubuntu splash — a failure only visible on
# a television, which is the worst place to discover it.
rm -rf "$WORK/gadget-check"
unsquashfs -q -f -d "$WORK/gadget-check" "$GADGET_SNAP" splash cloud.conf >/dev/null 2>&1 || true

if [ -f "$WORK/vendor-logo.png" ]; then
  if [ -f "$WORK/gadget-check/splash/vendor-logo.png" ]; then
    echo "    splash: vendor-logo.png present in the gadget"
  else
    echo "vendor-logo.png did not make it into the gadget snap" >&2
    exit 1
  fi
fi


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

echo "==> system-user assertion"
# Gives the image an account to SSH into. console-conf stays disabled, so the kiosk
# still comes up on its own; this only adds a way in when something needs looking at
# on the device — which four separate faults have now needed.
#
# Key-only: no password is generated or stored. Only the holder of the matching
# private key can log in, so publishing the public half in this repository costs
# nothing.
SYSTEM_USER="${SYSTEM_USER:-visiontak}"
SYSTEM_USER_EMAIL="${SYSTEM_USER_EMAIL:-visiontak@xfradio.net}"
KEYS_FILE="${SSH_KEYS_FILE:-$HERE/authorized-keys.pub}"
ASSERTION_ARGS=""

if [ -s "$KEYS_FILE" ]; then
  # JSON array of the non-comment, non-empty lines.
  SSH_KEYS_JSON=$(awk 'NF && $0 !~ /^#/ {
      gsub(/"/, "\\\"")
      printf "%s\"%s\"", (n++ ? "," : ""), $0
    }' "$KEYS_FILE")
  cat > "$WORK/system-user.json" <<EOF
{
  "type": "system-user",
  "authority-id": "$BRAND_ID",
  "brand-id": "$BRAND_ID",
  "email": "$SYSTEM_USER_EMAIL",
  "username": "$SYSTEM_USER",
  "name": "VisionTAK device access",
  "ssh-keys": [$SSH_KEYS_JSON],
  "series": ["16"],
  "models": ["visiontak-pi-arm64"],
  "since": "$STAMP",
  "until": "$(date -u -d '+5 years' +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
  if snap sign -k "$MODEL_KEY" "$WORK/system-user.json" > "$WORK/system-user.assert" \
       2> "$WORK/system-user.err"; then
    ASSERTION_ARGS="--assertion $WORK/system-user.assert"
    echo "    ssh access for '$SYSTEM_USER' signed"
  else
    # Not fatal: an image without SSH still boots and runs the kiosk.
    echo "    could not sign the system-user assertion, continuing without SSH:" >&2
    cat "$WORK/system-user.err" >&2
  fi
else
  echo "    no $KEYS_FILE — image will have no login" >&2
fi

echo "==> ubuntu-image"
# grade: dangerous is what allows the locally built, unsigned client and gadget in.
# --validation=ignore is the behaviour this build already relies on, and ubuntu-image
# warns that the default is changing. State it rather than inheriting a default that
# will flip to enforce — locally built, unsigned snaps have no validation sets to meet.
# shellcheck disable=SC2086 # ASSERTION_ARGS is deliberately word-split, or empty
sudo ubuntu-image snap "$WORK/model.assert" -O "$OUT" \
  --validation=ignore \
  $ASSERTION_ARGS \
  --snap "$GADGET_SNAP" \
  --snap "$CLIENT_SNAP"
sudo chown -R "$(id -u):$(id -g)" "$OUT"

echo "==> compress"
IMG="$(find "$OUT" -maxdepth 1 -name '*.img' | head -1)"
[ -n "$IMG" ] || { echo "ubuntu-image produced no .img" >&2; exit 1; }

# ubuntu-image names the file after the gadget volume, which is "pi" — that describes
# the board, not what is on the card. Rename before compressing so the artefact says
# what it is when it is sitting in a downloads folder next to other Pi images.
IMAGE_NAME="${IMAGE_NAME:-visiontak_client}"
TARGET="$OUT/$IMAGE_NAME.img"
[ "$IMG" = "$TARGET" ] || mv "$IMG" "$TARGET"

xz -T0 -9 "$TARGET"
echo "    $TARGET.xz"

echo
ls -lh "$OUT"
