# Local Ubuntu Core VM

A real Ubuntu Core 24 guest on the workstation, so the snap can be installed and run
the way it will be on a display — strict confinement, `snapd` interfaces, Ubuntu Frame
owning the only Wayland surface — without a Pi on the desk.

What it does **not** cover: HDMI-CEC (no `/dev/cec0`, no gadget slot — set
`cec-backend=none`) and arm64 (the guest is amd64; `snapcraft.yaml` builds both, but
only the amd64 artefact is exercised here). Everything above the CEC layer is real.

The [`docker/`](../docker) harness is the faster loop for pure UI work — Weston stands
in for Frame in a container. Use this VM when the question is about *confinement,
interfaces, hooks or the daemon lifecycle*, which a container cannot answer.

## Host requirements

An Ubuntu 24.04 machine with `/dev/kvm`. On Windows that is a WSL2 distro:

```powershell
wsl --install -d Ubuntu-24.04 --no-launch
```

`/dev/kvm` inside WSL needs nested virtualisation in `%USERPROFILE%\.wslconfig`:

```ini
[wsl2]
nestedVirtualization=true
memory=10GB
processors=8
```

then `wsl --shutdown` (this also stops Docker Desktop's VM — restart Docker Desktop
afterwards). Without KVM the guest still boots, on the TCG interpreter, at a speed that
makes WebKit unusable.

24.04 is not incidental: `--destructive-mode` builds on the host filesystem, and 24.04
*is* the `core24` base. Building on any other release links against the wrong libraries.

## Setup

```sh
wsl -d Ubuntu-24.04 -u root -- /mnt/c/…/VisionTAK_client/vm/setup-host.sh
```

Installs QEMU + OVMF + snapcraft and downloads `ubuntu-core-24-amd64.img` (450 MB) into
`/var/lib/visiontak-vm`. Idempotent.

## First boot — the one manual step

```sh
vm/run.sh --headless
```

Ubuntu Core's `console-conf` runs on first boot and **requires an Ubuntu One account
with an SSH key uploaded** to <https://login.ubuntu.com/ssh-keys>. There is no way
around it on a Canonical-signed image: the only alternative is building a
`grade: dangerous` image with your own signed model assertion, which needs the same
account anyway.

`setup-host.sh` generates `/var/lib/visiontak-vm/ssh_key` for this. Upload the `.pub`
to that page *before* booting — console-conf imports whatever public keys the account
holds at that moment, and it does not re-check later. Uploading a key you have no
private half of gets you a guest you cannot log into.

Answer the network prompt with the defaults and give it your Ubuntu One email. It
prints the account name it created — that is `VM_USER` below, and it is not always
your email's local part. Then:

```sh
ssh -p 8022 <user>@localhost
```

This happens once. `vm/run.sh --reset` throws it away and you do it again, so take the
snapshot below instead when you have a guest you like.

## Getting into the guest

QEMU forwards the guest's port 22 to **8022 on the host that runs `run.sh`** — the WSL
distro, not Windows. So the reliable entry point is from inside that distro:

```powershell
wsl -d Ubuntu-24.04 -u root
```
```sh
cd /mnt/c/…/VisionTAK_client
VM_USER=<you> vm/ssh.sh                       # shell
VM_USER=<you> vm/ssh.sh snap logs -n 20 visiontak-client
VM_USER=<you> vm/diag.sh                      # changes, disk, memory, snapd journal
```

Root, because the SSH key and the qemu process both live there.

There are two other ways in, each for a different job:

- **Serial console** — whatever terminal ran `vm/run.sh` is the guest's serial console.
  It is the only way in before `console-conf` has created a user, and the only way to
  watch the kernel boot. `Ctrl-A X` kills the VM.
- **QEMU monitor** — `/var/lib/visiontak-vm/monitor.sock`, for `system_powerdown` and
  friends. `vm/poweroff.sh` wraps the useful case.

To actually *see* the kiosk rather than drive it over SSH, the guest has to boot with a
display; `--headless` gives it none. Restart it:

```sh
sh vm/poweroff.sh
sh vm/run.sh          # window via WSLg, or use --vnc for a VNC server on 5901
```

## Daily use

```sh
vm/run.sh                                  # boot, window on the desktop via WSLg
vm/run.sh --gtk --detach                   # …and outlive this shell
VM_USER=rob vm/deploy.sh                   # build, install, configure, tail logs
VM_USER=rob vm/deploy.sh --skip-build      # reinstall the last build
vm/run.sh --reset                          # back to a pristine image
```

Without `--detach` the guest is a child of the shell that started it and dies with it —
a closed terminal, a dropped ssh session, an agent's background job. `--detach`
`setsid`s it, sends the serial console to `serial.log` instead of your terminal, and
verifies qemu is still alive before reporting success:

```sh
vm/run.sh --gtk --detach
tail -f /var/lib/visiontak-vm/serial.log
sh vm/poweroff.sh
```

Only one guest can hold the overlay at a time. `run.sh` now refuses to start a second
rather than letting qemu fail on a qcow2 write lock with an error that never mentions
the running VM.

Point the client at a server running on the Windows host — the guest reaches it through
QEMU's user-mode gateway at `10.0.2.2`:

```sh
VM_USER=rob SERVER_URL=http://10.0.2.2:8080 API_TOKEN=… vm/deploy.sh
```

## Snapshots

Take one as soon as `console-conf` is done — that is the step you never want to repeat.

```sh
vm/snapshot.sh save clean          # powers the guest down first, then copies
vm/snapshot.sh restore clean
vm/snapshot.sh list
```

QEMU's own `savevm` is not usable here: the UEFI varstore is a raw pflash device and
raw has no internal snapshot support, so `savevm` fails with *"Device 'pflash1' is
writable but does not support snapshots"*. A cold copy of the qcow2 overlay plus the
varstore is the guest's entire state, so nothing is lost by doing it this way.

`vm/run.sh --reset` throws away the overlay and sends you back to `console-conf`;
`vm/snapshot.sh restore clean` is almost always what you actually wanted.

## What to check in the guest

```sh
snap logs -f visiontak-client
sudo snap set visiontak-client refresh-interval=notanumber   # must FAIL, not crash-loop
```

That last one is the `configure` hook doing its job: a bad `snap set` is rejected at
set time rather than leaving the daemon restarting every 5s. An *incomplete* config is
not rejected — snapd runs the hook during `snap install`, before anything can be set,
so failing there would make the snap impossible to install.

For confinement, read the generated apparmor profile rather than the connection list.
`x11`, `desktop-legacy`, `desktop` and `gsettings` are declared on every snap the
`gnome` extension wraps and cannot be removed; on Core they stay unconnected and put
nothing in the profile:

```sh
P=/var/lib/snapd/apparmor/profiles/snap.visiontak-client.daemon
grep -c '/tmp/.X11-unix' $P    # must be 0
grep -nE '/dev/tty|/dev/cec' $P
```

`hdmi-cec` has no gadget slot on a stock image, so it should be unconnected. If snapd
auto-connects it to `console-conf:terminal-devices` — a different `custom-device`
attribute entirely — the snap silently gains `/dev/tty[0-9]` and `/dev/ttyS[0-9]`.
`deploy.sh` detects that and disconnects it.

## Layout

| Path | What |
|---|---|
| `vm/setup-host.sh` | One-time host provisioning, SSH key, image download |
| `vm/run.sh` | Boot the guest under QEMU/KVM |
| `vm/deploy.sh` | Build the snap, install it in the guest, configure, tail logs |
| `vm/ssh.sh` | Shell into the guest, or run one command in it |
| `vm/snapshot.sh` | Save/restore guest state so `console-conf` is done once |
| `vm/wait-ssh.sh` | Block until the guest accepts SSH — for scripted runs |
| `vm/poweroff.sh` | Clean ACPI shutdown via the QEMU monitor |
| `vm/diag.sh` | Dump guest snap changes, disk, memory and snapd journal |
| `/var/lib/visiontak-vm/` | Base image, qcow2 overlay, UEFI vars, SSH key, snapshots, build tree |

`deploy.sh` honours `VM_FOLLOW=0` to print the last 50 log lines and exit instead of
following, which is what you want from CI or any non-interactive shell.
