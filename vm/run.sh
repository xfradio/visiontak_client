#!/bin/sh
# Boot the Ubuntu Core 24 test VM under QEMU/KVM.
#
#   vm/run.sh                 # graphical window (WSLg / X11), serial on this terminal
#   vm/run.sh --headless      # no window, serial only — for CI and first-boot setup
#   vm/run.sh --vnc           # expose a VNC server on 5901 instead of a local window
#   vm/run.sh --reset         # discard all guest state, then boot a pristine image
#   vm/run.sh --reset-only    # discard guest state and stop
#   vm/run.sh --secboot       # UEFI Secure Boot (needs SMM — not available under WSL)
#   vm/run.sh --detach        # outlive this shell; serial goes to serial.log
#
# The guest never writes to the downloaded image: it boots a qcow2 overlay backed by
# it, so `--reset` is an unlink rather than a re-download, and a botched snap install
# costs nothing to undo.
set -eu

STATE_DIR="${VM_STATE_DIR:-/var/lib/visiontak-vm}"
BASE_IMAGE="$STATE_DIR/ubuntu-core-24-amd64.img"
OVERLAY="$STATE_DIR/disk.qcow2"
VARS_PREFIX="$STATE_DIR/OVMF_VARS"
MONITOR="$STATE_DIR/monitor.sock"
SERIAL_LOG="$STATE_DIR/serial.log"

reset_state() { rm -f "$OVERLAY" "$VARS_PREFIX".*; echo "guest state discarded"; }

SSH_PORT="${VM_SSH_PORT:-8022}"
VNC_PORT="${VM_VNC_PORT:-5901}"
MEM="${VM_MEM:-4096}"
CPUS="${VM_CPUS:-4}"
DISPLAY_MODE=auto
SECBOOT=0
DETACH=0
USE_GL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --headless) DISPLAY_MODE=none ;;
    --vnc)      DISPLAY_MODE=vnc ;;
    --gtk)      DISPLAY_MODE=gtk ;;
    --detach)   DETACH=1 ;;
    --gl)       USE_GL=1 ;;
    --reset)    reset_state ;;
    --reset-only) reset_state; exit 0 ;;
    --secboot)  SECBOOT=1 ;;
    -h|--help)  sed -n '2,14s/^# \{0,1\}//p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

[ -f "$BASE_IMAGE" ] || { echo "no image at $BASE_IMAGE — run vm/setup-host.sh" >&2; exit 1; }

# Secure Boot is off by default. Ubuntu's secure-boot OVMF is built with SMM_REQUIRE
# so the varstore cannot be rewritten from the guest, and SMM is exactly what WSL2's
# nested KVM does not implement: the guest dies with "KVM: entry failed, hardware
# error 0xffffffff" a fraction of a second into kernel boot. Ubuntu Core boots fine
# unverified — the image is not using FDE — and none of what this VM tests (snap
# confinement, interfaces, hooks, the daemon) depends on the firmware trust chain.
# Use --secboot on a host with real SMM if you are specifically testing that.
if [ "$SECBOOT" -eq 1 ]; then
  CODE=/usr/share/OVMF/OVMF_CODE_4M.ms.fd
  VARS_TEMPLATE=/usr/share/OVMF/OVMF_VARS_4M.ms.fd
  MACHINE=q35,smm=on
else
  CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
  VARS_TEMPLATE=/usr/share/OVMF/OVMF_VARS_4M.fd
  MACHINE=q35,smm=off
fi
[ -f "$CODE" ] || { echo "missing $CODE — apt install ovmf" >&2; exit 1; }

# Two instances cannot share the overlay. Without this the second one dies on a qcow2
# write lock, and the error says nothing about there already being a guest running.
if pgrep -f 'qemu-system-x86_64 -machine' >/dev/null 2>&1; then
  echo "a guest is already running (pid $(pgrep -f 'qemu-system-x86_64 -machine' | head -1))." >&2
  echo "stop it first:  sh $(dirname "$0")/poweroff.sh" >&2
  exit 1
fi

# The stock image's writable partition is ~1.7 GiB, which ubuntu-frame plus the gnome
# content snaps plus this snap overrun — snapd then dies mid-install with
# "cannot communicate with server: ... EOF". Give the overlay room and let
# snap-bootstrap grow /writable into it on first boot. qcow2 is sparse, so the larger
# virtual size costs nothing until it is used.
if [ ! -f "$OVERLAY" ]; then
  qemu-img create -q -f qcow2 -F raw -b "$BASE_IMAGE" "$OVERLAY"
  qemu-img resize -q "$OVERLAY" "${VM_DISK:-32G}"
fi
# Keyed on the firmware build: the two varstores are not interchangeable, and reusing
# one across a --secboot switch gives a firmware that hangs rather than an error.
VARS="$VARS_PREFIX.$(basename "$VARS_TEMPLATE" .fd)"
[ -f "$VARS" ] || cp "$VARS_TEMPLATE" "$VARS"

set -- \
  -machine "$MACHINE" \
  -m "$MEM" -smp "$CPUS" \
  -global ICH9-LPC.disable_s3=1 \
  -drive "if=pflash,format=raw,unit=0,file=$CODE,readonly=on" \
  -drive "if=pflash,format=raw,unit=1,file=$VARS" \
  -drive "if=none,id=disk0,format=qcow2,file=$OVERLAY" \
  -device virtio-blk-pci,drive=disk0,bootindex=1 \
  -netdev "user,id=net0,hostfwd=tcp::${SSH_PORT}-:22" \
  -device virtio-net-pci,netdev=net0 \
  -device virtio-rng-pci \
  -monitor "unix:$MONITOR,server,nowait"

# Detached, stdio is a log file rather than a terminal, so the multiplexed
# "mon:stdio" console would have nothing to attach to.
if [ "$DETACH" -eq 1 ]; then
  set -- "$@" -serial "file:$SERIAL_LOG"
else
  set -- "$@" -serial mon:stdio
fi

if [ -e /dev/kvm ] && [ -w /dev/kvm ]; then
  set -- "$@" -enable-kvm -cpu host
else
  echo "warning: /dev/kvm unusable — falling back to TCG, expect minutes to boot" >&2
  set -- "$@" -cpu max
fi

# Ubuntu Frame drives the display through DRM/KMS, so the guest needs a real GPU
# device, not the default stdvga text console.
#
# Probe for a socket rather than trusting $DISPLAY/$WAYLAND_DISPLAY: WSL exports both
# unconditionally, so testing the variables picks gtk even where nothing is listening
# and qemu then exits with "could not connect to display".
has_gui() {
  [ -n "${WAYLAND_DISPLAY:-}" ] && [ -S "${XDG_RUNTIME_DIR:-/run/user/0}/$WAYLAND_DISPLAY" ] && return 0
  [ -n "${DISPLAY:-}" ] && [ -S "/tmp/.X11-unix/X${DISPLAY#*:}" ] && return 0
  return 1
}

case "$DISPLAY_MODE" in
  auto)
    if has_gui; then DISPLAY_MODE=gtk; else DISPLAY_MODE=vnc; fi ;;
  gtk)
    has_gui || echo "warning: no wayland or X11 socket found; gtk will likely fail" >&2 ;;
esac

case "$DISPLAY_MODE" in
  # --gl hands guest OpenGL to the host through virglrenderer. Off by default because
  # it needs qemu built against virglrenderer AND a host EGL stack; when either is
  # missing qemu exits at startup rather than degrading. Worth it for dashboards that
  # embed heavy canvas/WebGL content — llvmpipe pegs every vcpu on those.
  gtk)  if [ "$USE_GL" -eq 1 ]; then
          set -- "$@" -device virtio-vga-gl -display gtk,gl=on,show-cursor=off
        else
          set -- "$@" -device virtio-vga -display gtk,show-cursor=off
        fi ;;
  # VNC is not a -display backend ("Available display backend types: none gtk sdl
  # egl-headless curses dbus") — it is its own top-level option.
  vnc)  set -- "$@" -device virtio-vga -vnc "0.0.0.0:$((VNC_PORT - 5900))"
        echo "VNC on localhost:$VNC_PORT" ;;
  none) set -- "$@" -device virtio-vga -display none ;;
esac

cat <<EOF
ubuntu-core-24-amd64  ${MEM}MiB  ${CPUS} vcpu  display=$DISPLAY_MODE
  ssh      ssh -p $SSH_PORT <user>@localhost
  host     reachable from the guest as 10.0.2.2 (a server on your workstation
           is http://10.0.2.2:<port> from inside the VM)
  quit     Ctrl-A X, or 'system_powerdown' via $MONITOR

EOF

# setsid detaches from the controlling terminal, so the guest is not collected when
# the shell that launched it goes away — an agent's background job, an ssh session
# that drops, a closed terminal.
if [ "$DETACH" -eq 1 ]; then
  : > "$SERIAL_LOG"
  setsid qemu-system-x86_64 "$@" >>"$SERIAL_LOG" 2>&1 < /dev/null &
  sleep 2
  if pgrep -f 'qemu-system-x86_64 -machine' >/dev/null 2>&1; then
    echo "detached. serial: tail -f $SERIAL_LOG"
    echo "stop with:      sh $(dirname "$0")/poweroff.sh"
  else
    echo "qemu exited immediately — last serial output:" >&2
    tail -20 "$SERIAL_LOG" >&2
    exit 1
  fi
  exit 0
fi

exec qemu-system-x86_64 "$@"
