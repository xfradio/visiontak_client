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

# snapcraft runs in destructive mode under sudo and leaves root-owned files behind in
# the gadget's parts/ tree, so the invoking user cannot clear them on the next run —
# a plain `rm -rf` dies with "Permission denied" partway through. That makes any
# second run of this script fail, locally as much as in CI. Escalate only for the
# leftovers, and only after the unprivileged attempt has done what it can.
if ! rm -rf "$WORK" "$OUT" 2>/dev/null; then
  sudo rm -rf "$WORK" "$OUT"
fi
mkdir -p "$WORK" "$OUT"

# Read here rather than at its first use: three separate pieces are keyed off it — the
# slot, the first-boot connect and the cloud.conf that performs it — and the first of
# them is needed before the gadget is even cloned. The reasoning behind the switch is
# with the slot itself, further down.
CEC_SLOT="${CEC_SLOT:-1}"

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

# cloud.conf is the documented UC20+ route for cloud-init config, and it is now the
# only route left for connecting hdmi-cec on first boot: the gadget.yaml `connections:`
# stanza is inert for unasserted snaps, for reasons set out where it used to be built,
# further down.
#
# An earlier attempt at this shipped the config on its own and it never executed —
# with no datasource to find, cloud-init reports itself untriggered and Ubuntu Core
# writes /etc/cloud/cloud-init.disabled before any module has run. Verified on
# hardware. image/gadget-cloud.conf therefore declares a `None` datasource, which is
# cloud-init's documented answer to "the config is already on disk"; that is the whole
# difference between the two attempts.
#
# CEC_SLOT=0 drops this along with the slot: without the slot the connect would fail,
# and a failed runcmd is one more thing to explain on a board that has CEC turned off
# on purpose. It also drops the console-conf masking the same file does, which suits
# the one situation CEC_SLOT=0 exists for — a build made to find out what a stuck
# device is saying, where text on the console VT is the point.
if [ "$CEC_SLOT" != "0" ]; then
  # Staged under a different name on purpose: an organize entry whose source and
  # destination are the same path is rejected by snapcraft.
  cp "$HERE/gadget-cloud.conf" "$EXTRAS/vt-cloud.conf"
  ORGANIZE="$ORGANIZE      vt-cloud.conf: cloud.conf\n"
fi

# image/cloud-init.yaml (the DHCP option 225 request) is still not shipped. It could
# ride in the same cloud.conf now that one runs, but it is a separate change and this
# mechanism has yet to prove itself on hardware. See docs/dhcp-discovery.md.

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
#
# CEC_SLOT=0 omits the slot *and* the cloud.conf that connects to it. A gadget snapd will
# not accept fails first-boot seeding outright, and the device then sits on
# "Installing Ubuntu Core" forever with no console and no SSH account to ask why —
# the two are created by the seeding that never finished. This switch exists to take
# CEC out of the picture in one build rather than guessing at it across several.
if [ "$CEC_SLOT" = "0" ]; then
  echo "==> hdmi-cec slot SKIPPED (CEC_SLOT=0) — remote control will not work"
elif ! grep -q '^  hdmi-cec:' "$WORK/pi-gadget/snapcraft.yaml"; then
  # Both lists are generated from this one, because they must agree and nothing catches
  # it when they do not. snapd matches each udev-tagging "kernel" tag *literally*
  # against the basenames in `devices` — it does not expand globs — so the pairing
  # /dev/cec0 + kernel: cec[0-9] is rejected with
  #   invalid "kernel" tag: "cec[0-9]" does not match any specified device
  # and snapd then drops the entire slot. The plug stays unconnected, opening
  # /dev/cec0 fails with EPERM, and the only trace is `snap warnings` — the image
  # builds, installs and runs while the remote is simply dead. This has now shipped
  # broken twice, in two different ways, so the two lists no longer exist separately.
  CEC_DEVICES="${CEC_DEVICES:-cec0 cec1}"
  awk -v devs="$CEC_DEVICES" '
    /^slots:/ && !ins {
      print
      print "  hdmi-cec:"
      print "    interface: custom-device"
      print "    custom-device: hdmi-cec"
      n = split(devs, d, " ")
      print "    devices:"
      for (i = 1; i <= n; i++) printf "      - /dev/%s\n", d[i]
      print "    udev-tagging:"
      for (i = 1; i <= n; i++) {
        printf "      - kernel: %s\n", d[i]
        print  "        subsystem: cec"
      }
      ins = 1
      next
    }
    { print }
    END { if (!ins) exit 3 }
  ' "$WORK/pi-gadget/snapcraft.yaml" > "$WORK/snapcraft.yaml.slots" \
    || { echo "no slots: stanza in the gadget snapcraft.yaml to extend" >&2; exit 1; }
  mv "$WORK/snapcraft.yaml.slots" "$WORK/pi-gadget/snapcraft.yaml"

  # Prove the invariant rather than trusting the generator above. snapd only reports
  # this on the device, as a warning nobody reads, long after the image was built.
  for dev in $CEC_DEVICES; do
    grep -q "^      - /dev/$dev\$" "$WORK/pi-gadget/snapcraft.yaml" \
      && grep -q "^      - kernel: $dev\$" "$WORK/pi-gadget/snapcraft.yaml" \
      || { echo "hdmi-cec slot is inconsistent for $dev — snapd would drop it" >&2; exit 1; }
  done
  echo "    hdmi-cec slot declared for: $CEC_DEVICES"
  sed -n '/^  hdmi-cec:/,/^  [a-z]/p' "$WORK/pi-gadget/snapcraft.yaml" | sed 's/^/      /'
fi

# The stock gadget sizes ubuntu-seed for Canonical's own image. This one additionally
# seeds ubuntu-frame, mesa-2404, gnome-46-2404, gtk-common-themes and the client —
# about 1.4 GiB against a 1200M partition. mkfs reports only "Disk full" without
# naming the structure, so the cause is not obvious from the failure.
# The stock gadget boots with fake KMS, where the firmware owns the display and no
# Linux CEC adapter is ever registered — /dev/cec0 simply does not exist, and dmesg
# mentions no cec at all. Full KMS is what exposes the vc4 CEC adapter, which is the
# entire basis of remote control here.
# The gadget allocates 128 MB of contiguous memory to the GPU for every model. That
# suits a Pi 3 B+, where 256 would be a quarter of the board's entire memory, and is
# modest for a Pi 4 driving a large panel with video.
CMA_MB="${CMA_MB:-128}"

# kms | fkms. Full KMS is the default because it is the only way /dev/cec0 exists —
# under fake KMS the firmware owns the display and no CEC adapter is ever registered.
# DISPLAY_DRIVER=fkms reverts, at the cost of the remote.
DISPLAY_DRIVER="${DISPLAY_DRIVER:-kms}"
CMDLINE="$WORK/pi-gadget/configs/cmdline.txt"

if [ "$DISPLAY_DRIVER" = "kms" ]; then
  # Per board, because the two boards disagree. Full KMS is the only way /dev/cec0
  # exists, and a Pi 3 B+ runs it happily. A Pi 4 under full KMS produced no output at
  # all — not the overlay name, which the firmware's overlay_map already resolves to
  # the 2711 variant, and not hotplug. Rather than give up the remote on every board or
  # ship an image that shows nothing on a Pi 4, choose per model.
  #
  # A Pi 4 does light up under full KMS — it was not the driver, it was the mode. With
  # hdmi_group/hdmi_mode forced below it shows a picture, so it keeps CEC too.
  # PI4_DRIVER=fkms falls back to video-only if a particular panel disagrees.
  PI4_DRIVER="${PI4_DRIVER:-kms}"
  # A Pi 4 has 2-8 GiB against the Pi 3's 1 GiB shared with the GPU, so it can afford a
  # much larger contiguous pool. CMA is what the compositor and WebKit's accelerated
  # layers allocate from at 1080p, and 128M is a Pi 3 figure — undersizing it there
  # shows up as tearing and dropped frames rather than an error, so it is worth
  # spending the memory a Pi 4 actually has.
  PI4_CMA_MB="${PI4_CMA_MB:-256}"
  awk -v cma="$CMA_MB" -v pi4cma="$PI4_CMA_MB" -v pi4="$PI4_DRIVER" '
    /^dtoverlay=vc4-f?kms-v3d/ {
      # Every model the gadget supports gets exactly one overlay line. No [all]
      # fallback: it would load a second overlay on top of the matched one.
      printf "[pi4]\ndtoverlay=vc4-%s-v3d,cma-%s\n", pi4, pi4cma
      printf "[cm4]\ndtoverlay=vc4-%s-v3d,cma-%s\n", pi4, pi4cma
      printf "[pi3]\ndtoverlay=vc4-kms-v3d,cma-%s\n", cma
      printf "[cm3]\ndtoverlay=vc4-kms-v3d,cma-%s\n", cma
      printf "[pi2]\ndtoverlay=vc4-fkms-v3d,cma-%s\n", cma
      printf "[all]\n"
      next
    }
    { print }
  ' "$WORK/pi-gadget/configs/config.txt" > "$WORK/config.txt.new"
  mv "$WORK/config.txt.new" "$WORK/pi-gadget/configs/config.txt"
  echo "    pi4/cm4 -> $PI4_DRIVER, pi3/cm3 -> kms (CEC), pi2 -> fkms"

  # vt.handoff=2 hands the console to a firmware framebuffer that full KMS never
  # creates, so it is dropped along with fake KMS.
  #
  # That was once believed to be why the boot splash disappeared under full KMS. It is
  # not. Plymouth lives in the pi-kernel snap's initramfs and needs a DRM device at the
  # moment it starts; under full KMS that device comes from vc4, which is not in the
  # initramfs, so plymouth never takes the display and the text console is simply what
  # remains on screen. Confirmed on a Pi 4: the symptom is the raw console, not
  # Ubuntu's own logo — a plymouth that was running but could not find our artwork
  # would show the latter. Removing vt.handoff changed nothing, because the handoff
  # was never the cause.
  #
  # The kernel snap is upstream of this build, so full KMS and a plymouth splash
  # cannot both be had from here. CEC is the whole point of the appliance and full KMS
  # is the only way /dev/cec0 exists, so the splash is what gives way. What is worth
  # fixing is the *text*: a wall display scrolling kernel messages reads as a broken
  # computer, where a black screen reads as a device starting up. The branding then
  # arrives with the kiosk's own splash, which is held long enough to be seen.
  sed -i 's/ *vt\.handoff=2//' "$CMDLINE"

  # Move the kernel console off tty1. This does not move it off *screen*: the kernel
  # makes the last console=ttyN the foreground VT, so the panel then shows tty3
  # instead of tty1 and the text is exactly as visible as it was. Observed on a Pi 4
  # with this cmdline already in place. tty3 is kept anyway because it is where the
  # messages should live, and because /dev/console following it is what the next two
  # settings depend on — but silencing is what actually clears the screen.
  sed -i 's/console=tty1/console=tty3/' "$CMDLINE"

  # quiet still lets warnings and errors through; loglevel=0 silences those too, and
  # global_cursor_default=0 removes the blinking block that is otherwise the only
  # thing on an empty screen.
  #
  # systemd.show_status=false is the one that removes the text a television actually
  # shows. "[ OK ] Started ..." and the first-boot install progress are written by
  # systemd to /dev/console directly, not through printk, so quiet and loglevel are
  # both irrelevant to them — which is why the screen still scrolled with a cmdline
  # that had every other silencing option on it.
  for param in loglevel=0 vt.global_cursor_default=0 systemd.show_status=false; do
    grep -q "$param" "$CMDLINE" || sed -i "s/\$/ $param/" "$CMDLINE"
  done

  # Two console= entries are listed, one of them serial. Without this plymouth can
  # take the serial console for its output and draw nothing on HDMI. Kept for the day
  # the kernel snap ships vc4 in its initramfs and the splash becomes possible again.
  grep -q 'plymouth.ignore-serial-consoles' "$CMDLINE" \
    || sed -i 's/$/ plymouth.ignore-serial-consoles/' "$CMDLINE"

  echo "    silent boot: console -> tty3, loglevel=0, systemd status off, no cursor"
  echo "                 (plymouth cannot run under full KMS — the screen is black)"

  # Full KMS relies on the kernel negotiating a mode, where fake KMS inherited whatever
  # the firmware had already set up. On a Pi 4 that negotiation produced no picture at
  # all until the mode was stated outright — hotplug forcing alone was not enough.
  #
  # 1/16 is CEA 1080p60, which is what a television is. A panel that wants something
  # else needs HDMI_GROUP/HDMI_MODE; the table is in the Raspberry Pi config.txt
  # documentation. This is scoped to pi4 because that is the board that needed it.
  HDMI_GROUP="${HDMI_GROUP:-1}"
  HDMI_MODE="${HDMI_MODE:-16}"
  if ! grep -q 'hdmi_force_hotplug' "$WORK/pi-gadget/configs/config.txt"; then
    {
      printf '\n[pi4]\n'
      printf 'hdmi_force_hotplug=1\n'
      printf 'hdmi_group=%s\n' "$HDMI_GROUP"
      printf 'hdmi_mode=%s\n' "$HDMI_MODE"
      printf '[all]\n'
    } >> "$WORK/pi-gadget/configs/config.txt"
    echo "    pi4 HDMI forced to group=$HDMI_GROUP mode=$HDMI_MODE (1/16 = 1080p60)"
  fi

  # A Pi 4B is a 1.5 GHz part that the firmware will clock to 1.8 GHz when told to.
  # This is Raspberry Pi's own supported switch, not an overclock: no over_voltage,
  # no custom arm_freq, and boards without the newer firmware ignore it. Scoped to
  # pi4 because that is the only model it means anything on.
  ARM_BOOST="${ARM_BOOST:-1}"
  if [ "$ARM_BOOST" = "1" ] && ! grep -q 'arm_boost' "$WORK/pi-gadget/configs/config.txt"; then
    printf '\n[pi4]\narm_boost=1\n[all]\n' >> "$WORK/pi-gadget/configs/config.txt"
    echo "    pi4 arm_boost=1 (1.5 -> 1.8 GHz)"
  fi

  echo "    full KMS (vc4-kms-v3d); vt.handoff dropped so plymouth owns the console"
else
  echo "    fake KMS retained — /dev/cec0 will not exist and the remote will not work" >&2
  # fkms leaves a single overlay line the per-model rewrite never produced. Confined to
  # this branch: under KMS it would flatten the per-model CMA values back to one number,
  # undoing the larger pool a Pi 4 is given above.
  sed -i "s/\(dtoverlay=vc4-f\?kms-v3d\),cma-[0-9]*/\1,cma-$CMA_MB/" \
    "$WORK/pi-gadget/configs/config.txt"
fi
echo "    cmdline: $(cat "$CMDLINE")"

# The firmware waits a second before loading the kernel to let slow SD cards settle.
# Nothing here needs it, and it is pure latency on every boot.
grep -q '^boot_delay' "$WORK/pi-gadget/configs/config.txt" \
  || printf '\nboot_delay=0\n' >> "$WORK/pi-gadget/configs/config.txt"

echo "    CMA ${CMA_MB}M (pi4/cm4 ${PI4_CMA_MB:-$CMA_MB}M)"
grep -n 'dtoverlay=vc4\|arm_boost\|boot_delay\|^\[' "$WORK/pi-gadget/configs/config.txt" || true

# Both partitions are resized by matching the stock literal, so an upstream change to
# the gadget would silently leave the old geometry in place and produce an image that
# fails the same way as the last one. Fail the build instead — a wrong size is only
# discovered on hardware, half an hour later, with nothing on screen to explain it.
resize_partition() {
  part="$1"; from="$2"; to="$3"
  if ! grep -q "^ *size: $from\$" "$WORK/pi-gadget/gadget.yaml"; then
    echo "gadget.yaml has no '$part' partition at the expected $from —" >&2
    echo "upstream changed it. Re-check the sizes before shipping this image." >&2
    grep -n 'name:\|size:' "$WORK/pi-gadget/gadget.yaml" >&2
    exit 1
  fi
  sed -i "s/^\( *\)size: $from\$/\1size: $to/" "$WORK/pi-gadget/gadget.yaml"
  echo "    $part sized to $to (was $from)"
}

resize_partition ubuntu-seed 1200M "${SEED_SIZE:-2500M}"

# ubuntu-data is where first boot installs every seeded snap, and the gadget fixes it
# at 1500M with its own "XXX: make auto-grow to partition" note. The seeded set —
# kernel, core24, mesa, gnome-46-2404, gtk-common-themes, ubuntu-frame and this client
# — was already close to that, and adding the GStreamer decoders took the client from
# 68 MB to 142 MB. Install then stops partway with "Installing Ubuntu Core" still on
# screen and no error anywhere reachable, because there is no console yet.
#
# Sized for the 16 GB card these units are built on: seed + boot + save + data comes
# to about 11.2 GB, inside the ~14.8 GiB such a card really has. ubuntu-data does not
# grow later — the gadget's own "XXX: make auto-grow to partition" note is still
# unaddressed upstream — so whatever is set here is what the device lives with, and
# snap refreshes keep old revisions around. Drop to 2500M for an 8 GB card.
resize_partition ubuntu-data 1500M "${DATA_SIZE:-8G}"

grep -n 'name:\|size:' "$WORK/pi-gadget/gadget.yaml"

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
  # `console-conf: disable: true` skips the setup wizard. It does not stop the unit:
  # console-conf@<console-tty>.service still runs and still puts a login prompt on the
  # VT the panel is showing. Masking it is done from cloud.conf — see gadget-cloud.conf.
  echo "    console-conf wizard disabled, ubuntu-frame set to run as a daemon"
fi

# There is deliberately no `connections:` stanza in gadget.yaml. It is the documented
# way to auto-connect an interface at first boot, three versions of it have shipped
# here, and none of them connected anything, because it cannot work on an image built
# this way. From snapd, overlord/ifacestate/helpers.go, addGadgetConnections:
#
#   snapID := snapInfo.SnapID
#   if snapID == "" {
#       // not a snap-id identifiable snap, skip
#       return nil
#   }
#
# A snap-id comes from a snap-declaration assertion, which only the store issues. Every
# snap this script builds locally — the client and the pi gadget both — is unasserted
# and has none, so the stanza is skipped before a single entry is looked at. That is
# why `snap tasks 1` showed no Connect task for hdmi-cec at all, not even a failed one,
# while every other connection in the seed produced one: the others are store snaps.
#
# Past the check it would fail again anyway. Entries are matched by comparing
# gconn.Plug.SnapID against the real snap-id, and the other side is put through
# resolveSnapIDToName, which returns "" for a snap with no declaration — so
# `slot: pi:hdmi-cec` resolves to repo.Slot("", "hdmi-cec"), nil, and is logged as
# "gadget connections: ignoring missing slot". Snap *names* in the snap-id position
# never worked; that they parse is not the same as that they resolve.
#
# So this route needs both snaps published to a store (a brand store counts) and the
# gadget.yaml written with their actual snap-ids. Until then the connection is made on
# first boot by cloud-init, from image/gadget-cloud.conf, which is shipped as the
# gadget's cloud.conf above.
#
# If that has not run — a reflash interrupted, or CEC_SLOT=0 — the fallback stays what
# it has always been:
#
#   sudo snap connect visiontak-client:hdmi-cec pi:hdmi-cec
#
# The client says exactly that in its log when the open is refused with EPERM, because
# this has been rediscovered from a bare "Operation not permitted" on three cards.

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

# Same reasoning as the logo, with more at stake: a gadget that builds without cloud.conf
# produces an image whose remote is dead, and the only symptom is on a television.
if [ "$CEC_SLOT" != "0" ]; then
  if grep -q 'snap connect\|visiontak-client:hdmi-cec' "$WORK/gadget-check/cloud.conf" 2>/dev/null; then
    echo "    cloud.conf: first-boot hdmi-cec connect present in the gadget"
  else
    echo "cloud.conf did not make it into the gadget snap, or does not connect" >&2
    echo "hdmi-cec — the image would boot with no remote control" >&2
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
