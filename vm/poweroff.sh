#!/bin/sh
# Ask the guest to shut down over the QEMU monitor and wait for QEMU to exit.
# Exits 0 if the guest was already off.
#
#   vm/poweroff.sh            # ACPI shutdown, wait up to 90s
#   vm/poweroff.sh --force    # SIGKILL qemu if ACPI does not take
#
# --force exists because a guest that has saturated every vcpu cannot service the
# ACPI interrupt, and then the clean path never completes.
set -eu

STATE_DIR="${VM_STATE_DIR:-/var/lib/visiontak-vm}"
MONITOR="$STATE_DIR/monitor.sock"
QEMU_PATTERN='qemu-system-x86_64 -machine'

FORCE=0
DEADLINE=90
case "${1:-}" in
  --force) FORCE=1 ;;
  ?*) DEADLINE="$1" ;;
esac

pgrep -f "$QEMU_PATTERN" >/dev/null 2>&1 || { echo "guest already off"; exit 0; }
[ -S "$MONITOR" ] || { echo "guest is running but $MONITOR is missing" >&2; exit 1; }

echo "powering down the guest..."
echo system_powerdown | nc -U -q 1 "$MONITOR" >/dev/null 2>&1 || true

i=0
while [ "$i" -lt "$DEADLINE" ]; do
  pgrep -f "$QEMU_PATTERN" >/dev/null 2>&1 || { echo "guest off after ${i}s"; exit 0; }
  i=$((i + 1))
  sleep 1
done

if [ "$FORCE" -eq 1 ]; then
  echo "ACPI shutdown did not take after ${DEADLINE}s — killing qemu" >&2
  pkill -KILL -f "$QEMU_PATTERN" || true
  sleep 2
  pgrep -f "$QEMU_PATTERN" >/dev/null 2>&1 && { echo "qemu survived SIGKILL" >&2; exit 1; }
  echo "guest killed"
  exit 0
fi

echo "guest did not shut down within ${DEADLINE}s (use --force to kill it)" >&2
exit 1
