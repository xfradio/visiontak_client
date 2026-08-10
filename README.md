# VisionTAK Client

A full-screen, TV-remote-driven client for VisionTAK Server, built for unattended
displays running **Ubuntu Core**.

```
┌─────────────────────────────────────────────┐
│  Ubuntu Core 24  (Pi 4/5, strict confinement)│
│                                              │
│   ubuntu-frame ──── Mir / Wayland ────┐      │
│     (compositor, only owner of the DRM)│     │
│                                        ▼     │
│   visiontak-client snap                      │
│     ├── GTK4 window  ── WebKitGTK 6.0 views  │
│     ├── CEC reader   ── /dev/cec0 ioctls     │
│     └── REST client  ── VisionTAK Server     │
└─────────────────────────────────────────────┘
```

## Why this shape

- **Ubuntu Frame (Mir), Wayland only.** No X11 socket exists on the image, so the
  "any client can keylog and screenshot every other client" property of X11 is
  structurally absent. Mir hands the kiosk its own surface and nothing else. The
  client refuses to start rather than fall back to X11.
- **Strict confinement.** The snap plugs `wayland`, `opengl`, `network`,
  `network-bind`, `browser-support` and one `custom-device` for CEC. No `home`, no
  `x11`, no `desktop-legacy`, no `system-files`.
- **CEC through the kernel, not libcec.** The Pi's vc4 driver already is a CEC adapter;
  we drive `/dev/cec0` with `ctypes` ioctls, so there is no C++ library in the trusted
  computing base. A libcec backend exists for x86 boxes with USB dongles.
- **One process owns everything.** Webviews and CEC live in the same GTK main loop, so
  switching dashboards is a stack transition, not a browser restart.
- **Zero runtime pip dependencies.** GTK/WebKit come from distro packages staged by
  snapcraft; everything else is stdlib.

## Layout

| Path | What |
|---|---|
| `src/visiontak_client/cec/` | Kernel uAPI bindings, kernel + libcec backends, reader thread, key map |
| `src/visiontak_client/ui/` | GTK4 app, kiosk window, chooser overlay, navigation allowlist |
| `src/visiontak_client/api.py` | REST client + on-disk config cache |
| `src/visiontak_client/controller.py` | Wires CEC, refresh loop and carousel to the window |
| `src/visiontak_client/probe.py` | Read-only pre-flight check against a live server |
| `snap/` | `snapcraft.yaml`, `configure`/`install` hooks |
| `vm/` | Local Ubuntu Core 24 VM for testing the built snap under real confinement |
| `assets/` | Brand artwork: kiosk splash, loading overlay, source for the boot splash |
| `docs/` | Image build, CEC details, API contract |

## Build and deploy

```bash
make snap                       # snapcraft --build-for=arm64 --use-lxd
make install-pi PI=ubuntu@10.0.0.5

# on device
sudo snap install ubuntu-frame
sudo snap set visiontak-client server-url=https://visiontak.example api-token=…
sudo snap connect visiontak-client:hdmi-cec pi:hdmi-cec
snap logs -f visiontak-client
```

A fully baked SD card image is built by CI — see [`docs/sd-image-ci.md`](docs/sd-image-ci.md).
For a bootable SD card without signing anything, burn Canonical's stock Ubuntu Core
arm64 image and install this snap onto the running device —
[`docs/sd-card-image.md`](docs/sd-card-image.md).

Full image assembly — gadget fork for the CEC `custom-device` slot, model assertion,
`ubuntu-image` — is in [`docs/ubuntu-core-image.md`](docs/ubuntu-core-image.md).

## Configuration

All via `snap set visiontak-client <key>=<value>`:

`server-url`, `api-token`, `device-id`, `cec-backend` (`auto`|`kernel`|`libcec`|`none`),
`cec-device`, `osd-name`, `refresh-interval`, `rotate-interval`, `start-dashboard`,
`verify-tls`, `allowed-hosts`, `max-live-views`, `hardware-acceleration`.
On a Pi 3 B+ set `max-live-views=1` and `hardware-acceleration=never` — see
[`docs/raspberry-pi-3.md`](docs/raspberry-pi-3.md). Defaults and validation rules are in
[`.pandaos/specs/S001-visiontak-client.md`](.pandaos/specs/S001-visiontak-client.md).

The `configure` hook validates the result with `--check-config` and **fails the
`snap set`** if it is unusable, rather than letting the daemon crash-loop.

## Remote control

Arrows navigate, OK opens the chooser and confirms, Back closes, 1–9 jump straight to a
dashboard, Ch±/◀▶ step through them, Green reloads, Yellow pauses the carousel, Blue
shows diagnostics. Full table in [`docs/cec.md`](docs/cec.md). The same actions are on
the keyboard for bring-up before CEC is wired.

## Development

```bash
make venv && make test && make lint
```

89 tests, no GTK, CEC hardware or server required — the kernel uAPI struct layouts and
ioctl numbers are pinned against the values in `linux/cec.h`, CEC message decoding runs
against synthesised frames, and the API tests use payloads captured verbatim from a
live server.

To exercise the packaged snap under real strict confinement without a Pi, there is a
local Ubuntu Core 24 VM — see [`vm/README.md`](vm/README.md):

```bash
make vm-setup                            # one-time: QEMU, snapcraft, Core image
make vm-run                              # boot the guest
VM_USER=<you> make vm-deploy             # build, install, configure, tail logs
```

It covers what a container cannot — snapd interfaces, the `configure` hook rejecting
bad input, and the daemon lifecycle under Ubuntu Frame. It does not cover CEC (no
`/dev/cec0` in a VM) or arm64.

Before deploying to a display, check the server from the device's point of view:

```bash
python -m visiontak_client.probe http://visiontak.example --token "$TOKEN"
```

It verifies the token is accepted, lists the dashboards the device is allowed, confirms
each `/view/{id}` page renders instead of redirecting to sign-in, and reports whether
the admin endpoints are exposed.

## Server contract

Confirmed against a live instance — see [`docs/api-contract.md`](docs/api-contract.md).
In short: `GET /api/v1/client/config` with a bearer token returns
`{defaultDashboardId, allowedDashboards:[{id, name}]}`, and each dashboard is rendered
at `{server}/view/{id}`. That page is deliberately outside the sign-in gate, which is
what makes a keyboard-less display possible.

Two things worth knowing:

- `/api/v1/dashboards` and `/api/v1/layouts` currently answer **with no token**. The
  kiosk does not use them, but anything that can reach the server can enumerate every
  dashboard and layout. Server-side fix.
- There is no device enrolment or heartbeat endpoint, so the server cannot currently
  tell which displays are alive or what they are showing.
