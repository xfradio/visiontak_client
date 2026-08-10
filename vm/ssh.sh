#!/bin/sh
# Shell into the guest, or run a command in it.
#
#   VM_USER=dexdeadly vm/ssh.sh                    # interactive shell
#   VM_USER=dexdeadly vm/ssh.sh snap logs -n 20 visiontak-client
#   VM_USER=dexdeadly vm/ssh.sh sudo snap set visiontak-client server-url=http://10.0.2.2:8080
#
# The key lives beside the rest of the VM state and is root-owned, so this generally
# runs as root on the host — which is also where qemu and the port forward live.
set -eu

STATE_DIR="${VM_STATE_DIR:-/var/lib/visiontak-vm}"
SSH_PORT="${VM_SSH_PORT:-8022}"
KEY="${VM_SSH_KEY:-$STATE_DIR/ssh_key}"
: "${VM_USER:?set VM_USER to the account console-conf created on first boot}"

[ -f "$KEY" ] || { echo "no private key at $KEY — run vm/setup-host.sh" >&2; exit 1; }

exec ssh -p "$SSH_PORT" -i "$KEY" -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=accept-new \
  -o UserKnownHostsFile="$STATE_DIR/known_hosts" \
  "$VM_USER@localhost" "$@"
