#!/bin/sh
# Save and restore the guest disk, so `console-conf` is done once and never again.
#
#   vm/snapshot.sh save clean       # power the guest down if needed, then copy
#   vm/snapshot.sh restore clean
#   vm/snapshot.sh list
#
# QEMU's own `savevm` cannot be used here: the UEFI varstore is attached as a raw
# pflash device and raw does not support internal snapshots, so savevm fails with
# "Device 'pflash1' is writable but does not support snapshots". A cold copy of the
# overlay plus the varstore is the whole of the guest's state anyway.
set -eu

STATE_DIR="${VM_STATE_DIR:-/var/lib/visiontak-vm}"
OVERLAY="$STATE_DIR/disk.qcow2"
VARS_PREFIX="$STATE_DIR/OVMF_VARS"
SNAP_DIR="$STATE_DIR/snapshots"

power_down() { sh "$(dirname "$0")/poweroff.sh"; }

usage() { sed -n '2,7s/^# \{0,1\}//p' "$0"; exit "${1:-0}"; }

CMD="${1:-}"
NAME="${2:-clean}"
case "$CMD" in
  save)
    power_down
    mkdir -p "$SNAP_DIR/$NAME"
    [ -f "$OVERLAY" ] || { echo "no guest disk at $OVERLAY" >&2; exit 1; }
    cp "$OVERLAY" "$SNAP_DIR/$NAME/disk.qcow2"
    for v in "$VARS_PREFIX".*; do
      [ -f "$v" ] && cp "$v" "$SNAP_DIR/$NAME/$(basename "$v")"
    done
    echo "saved '$NAME' ($(du -sh "$SNAP_DIR/$NAME" | cut -f1))"
    ;;
  restore)
    [ -d "$SNAP_DIR/$NAME" ] || { echo "no snapshot '$NAME'" >&2; exit 1; }
    power_down
    cp "$SNAP_DIR/$NAME/disk.qcow2" "$OVERLAY"
    for v in "$SNAP_DIR/$NAME"/OVMF_VARS.*; do
      [ -f "$v" ] && cp "$v" "$STATE_DIR/$(basename "$v")"
    done
    echo "restored '$NAME'"
    ;;
  list)
    [ -d "$SNAP_DIR" ] || { echo "(none)"; exit 0; }
    for d in "$SNAP_DIR"/*/; do
      [ -d "$d" ] || continue
      printf '%-20s %s\n' "$(basename "$d")" "$(du -sh "$d" | cut -f1)"
    done
    ;;
  ''|-h|--help) usage 0 ;;
  *) echo "unknown command: $CMD" >&2; usage 2 ;;
esac
