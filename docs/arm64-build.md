# Building the arm64 snap

The Pi needs an arm64 snap and **an x86 workstation cannot produce one for this
project**. Both routes are closed, for different reasons:

- **Cross-compiling** (`--build-for=arm64` on amd64) fails at `Installing build-snaps`.
  The `gnome` extension pulls `mesa-2404` and `gnome-46-2404-sdk` as build-snaps, and
  those must match the target architecture; snapd cannot install arm64 snaps into an
  amd64 build container. The extension does not support cross-building.
- **Emulating an arm64 container** fails earlier. `qemu-aarch64` binfmt registration is
  not sufficient: LXD refuses with *"Requested architecture isn't supported by this
  host"*. Docker-with-QEMU does not help either, because snapcraft still needs snapd to
  fetch those build-snaps.

So: build on real arm64. The amd64 snap for the local test VM is unaffected —
`make snap-amd64` still works on the workstation.

## Host requirements

| | |
|---|---|
| OS | **Ubuntu 24.04 arm64** — this *is* the `core24` base; anything else links wrong |
| RAM | 4 GB minimum, 8 GB comfortable |
| Disk | ~12 GB free — staging WebKit and the gnome SDK, not the source |
| Access | SSH |

A Pi 4/5 running Ubuntu Server 24.04 also qualifies. A Pi 3 B+ does not — 1 GB will not
stage WebKit.

## Providers

| | Notes |
|---|---|
| **Oracle Cloud, Ampere A1** | Always-Free tier covers 4 OCPU / 24 GB. Comfortably the cheapest fit; capacity in a given region can be hard to get. |
| **AWS Graviton** (`t4g.medium`) | Reliable, pennies per build, `t4g.small` is tight on RAM. |
| **Hetzner** (`CAX11`+) | Cheap, hourly billing, easy to destroy after. |

Any of them works; the scripts assume nothing beyond Ubuntu 24.04 arm64 and SSH. Nothing
here provisions cloud resources for you — spin the VM up yourself so the billing and
credentials stay in your hands.

## Build it

From this workstation, once the VM is up:

```sh
vm/remote-build.sh ubuntu@<vm-ip>
# or, with an explicit key:
BUILD_SSH_KEY=~/.ssh/oracle vm/remote-build.sh ubuntu@<vm-ip>
```

That pushes the tree (excluding `parts/`, `stage/` and `.venv/`, which hold x86
artefacts that would poison an arm64 build), runs the build, and pulls
`visiontak-client_*_arm64.snap` back into `dist/`.

To build by hand on the VM instead:

```sh
git clone <repo> && cd visiontak-client
sh vm/build-arm64.sh
```

`build-arm64.sh` refuses to run on a non-arm64 host rather than producing something
that will not install on the Pi.

## Then

Install it on the device following [`sd-card-image.md`](sd-card-image.md):

```sh
scp dist/visiontak-client_*_arm64.snap <user>@<pi>:/tmp/
ssh <user>@<pi> 'sudo snap install --dangerous /tmp/visiontak-client_*_arm64.snap'
```

Destroy the build VM afterwards — it holds a copy of the source and is not needed
between releases.

## Why not Launchpad

`snapcraft remote-build` builds natively on arm64 for free and is the obvious answer
where source is public. Launchpad builds are visible to anyone, so it is only an option
if this client is not proprietary.
