#!/bin/sh
# Ad-hoc guest diagnostics over SSH. Not part of the deploy path.
set -eu
STATE_DIR="${VM_STATE_DIR:-/var/lib/visiontak-vm}"
SSH="ssh -p ${VM_SSH_PORT:-8022} -i ${VM_SSH_KEY:-$STATE_DIR/ssh_key} -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$STATE_DIR/known_hosts \
  -o ConnectTimeout=10"
$SSH "${VM_USER:?}@localhost" '
echo "=== snap changes ==="; snap changes 2>&1 | tail -12
echo "=== snap list ==="; snap list 2>&1
echo "=== disk ==="; df -h / /writable 2>/dev/null | grep -v ^Filesystem || df -h
echo "=== memory ==="; free -m | head -2
echo "=== snapd service ==="; systemctl is-active snapd.service; systemctl show snapd.service -p NRestarts
echo "=== snapd journal (errors) ==="; sudo journalctl -u snapd --no-pager -n 40 --priority=warning 2>&1 | tail -25
echo "=== oom ==="; sudo dmesg 2>/dev/null | grep -i -E "out of memory|oom-kill" | tail -5 || echo "(none)"
'
