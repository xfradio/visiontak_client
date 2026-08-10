#!/bin/sh
# Block until the guest accepts SSH, so scripted runs do not race the boot.
#
#   vm/wait-ssh.sh dexdeadly [timeout-seconds]
set -eu

STATE_DIR="${VM_STATE_DIR:-/var/lib/visiontak-vm}"
SSH_PORT="${VM_SSH_PORT:-8022}"
KEY="${VM_SSH_KEY:-$STATE_DIR/ssh_key}"
USER_NAME="${1:-${VM_USER:-}}"
DEADLINE="${2:-180}"

[ -n "$USER_NAME" ] || { echo "usage: vm/wait-ssh.sh <user> [timeout]" >&2; exit 2; }

i=0
while [ "$i" -lt "$DEADLINE" ]; do
  if ssh -p "$SSH_PORT" -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes \
         -o StrictHostKeyChecking=accept-new \
         -o UserKnownHostsFile="$STATE_DIR/known_hosts" \
         -o ConnectTimeout=5 "$USER_NAME@localhost" true 2>/dev/null; then
    echo "ssh up after ${i}s"
    exit 0
  fi
  i=$((i + 3))
  sleep 3
done

echo "guest did not accept ssh within ${DEADLINE}s" >&2
exit 1
