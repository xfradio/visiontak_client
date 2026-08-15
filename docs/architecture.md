# VisionTAK Client — what it is, what it needs, and what it does

A single reference for the whole appliance: the snap, the image it ships in, the server
it talks to, and the hardware it runs on. The last section is the one worth reading if
you are considering starting over — it is an honest account of what this shape cost and
what a second attempt should do differently.

Companion documents go deeper on individual topics: [`api-contract.md`](api-contract.md),
[`cec.md`](cec.md), [`sd-image-ci.md`](sd-image-ci.md),
[`raspberry-pi-models.md`](raspberry-pi-models.md),
[`dhcp-discovery.md`](dhcp-discovery.md).

---

## 1. What it is

An unattended wall display. A Raspberry Pi is bolted behind a television, powered on,
and never touched again. It shows dashboards hosted by VisionTAK Server, and the only
input device is the television's own remote control over HDMI-CEC.

There is no keyboard, no mouse, no login prompt and no desktop. The device boots
directly into a full-screen browser surface and stays there.

Three properties drove every design decision:

- **Nobody is in front of it.** Anything that requires a human — a console prompt, a
  dialog, a click to start a video — is a failure. The device must recover on its own
  or it is dead until someone drives to it.
- **Nobody can log into it.** Field units run with `console-conf` disabled. Diagnosis
  happens through what the device shows on screen, what it logs, and an SSH key baked
  into the image.
- **It is a browser pointed at someone else's content.** Dashboards are authored on the
  server and embed third-party pages. The client is a rendering appliance, not an
  application, so confinement matters more than features.

## 2. What it does

**On first boot** it comes up on a branded splash and, having no server address, shows
an on-screen setup screen asking for one. The address is typed with a temporarily
attached keyboard — the only time one is needed.

**It enrols itself.** With an address it POSTs to `/api/v1/client/register` with a
stable device id. The server answers `pending` until an administrator approves the
device, so the client keeps asking every 20 seconds, across reboots, indefinitely.
The screen says why it is waiting. When approval lands the server returns a token,
once and only once, which the client persists.

**It then shows dashboards.** It polls `/api/v1/client/config` every 5 minutes for the
list it is allowed to display, renders each at `{server}/view/{id}` in its own WebKit
view, and keeps the most recent few alive so switching is instant. The last good list
is cached on disk, so a server outage leaves the screen showing dashboards rather than
an error.

**It is driven by the TV remote.** Arrows and OK open a chooser and step between
dashboards; number keys jump directly; coloured buttons reload, toggle the carousel and
show a diagnostics panel. The same actions are on the keyboard for bring-up.

**It reports itself.** An on-screen diagnostics overlay gives the device id, the
device's own IP address, the server, the current dashboard, and the live CEC state —
because on a wall-mounted unit with no login, that panel is the only thing that can
tell you what is wrong.

## 3. Requirements

### Hardware

| | |
|---|---|
| Board | Raspberry Pi 3 B+ or Pi 4 (arm64) |
| Memory | 1 GiB minimum; 4 GiB recommended |
| Storage | 16 GB SD card (8 GB minimum with `DATA_SIZE=2500M`) |
| Display | HDMI. CEC support on the television for remote control |
| Network | Ethernet or wifi with a route to the server |

The client reads total memory at startup and tunes itself: below 1.5 GiB it keeps one
dashboard live and forces software rendering; at 3 GiB and above it keeps six live.
A field unit has nobody to run `snap set` on it, so it has to size itself.

### Software

- **Ubuntu Core 24**, arm64
- **Ubuntu Frame** as the Wayland compositor — the only owner of the DRM device
- **GTK 4** and **WebKitGTK 6.0**, from the `gnome-46-2404` platform snap
- Python 3.12, standard library only at runtime — no third-party packages in the
  trusted path

### Server

Two endpoints, both under `/api/v1/client`:

```
POST /api/v1/client/register     {deviceId, deviceType, label}
  -> {"status": "pending"}
  -> {"status": "approved", "token": "<raw token>"}   (once)
  -> {"status": "approved", "token": null}            (already issued)

GET  /api/v1/client/config       Authorization: Bearer <token>
  -> {"defaultDashboardId": …, "allowedDashboards": [{"id": …, "name": …}]}
```

Dashboards carry no URL; each renders at `{server}/view/{id}`.

One trap worth stating: the server is a Next.js app with a catch-all route, so an
unknown path returns the SPA shell with **HTTP 200**. A 200 proves nothing. Every
response is checked for a JSON content type before it is believed.

## 4. Shape

```
Ubuntu Core 24  (arm64, strict confinement)
│
├── ubuntu-frame ............ Mir/Wayland compositor, owns the DRM device
│
└── visiontak-client snap
    ├── ui/app.py ........... GTK application, Wayland-only, key handling
    ├── ui/kiosk_window.py .. the surface: WebView stack, chooser, splash,
    │                         toast, diagnostics overlay, LRU eviction
    ├── ui/setup.py ......... first-run address entry
    ├── ui/policy.py ........ navigation allowlist
    ├── controller.py ....... wiring: refresh loop, enrolment polling,
    │                         carousel, CEC events -> actions
    ├── api.py .............. REST client and on-disk config cache
    ├── cec/ ................ kernel CEC via ctypes ioctls on /dev/cec0
    ├── config.py ........... layered configuration and board detection
    └── probe.py ............ off-device server checker
```

Everything touching GTK runs on the main loop. Network work and CEC reading run on
worker threads and marshal back through `idle()`, so a dead server can never stall the
compositor's client.

**Wayland only.** No X11 socket exists on the image. The client refuses to start rather
than fall back, because falling back would silently discard the isolation that
motivated Mir in the first place.

**CEC through the kernel, not libcec.** The Pi's vc4 driver already *is* a CEC adapter.
Driving `/dev/cec0` with `ctypes` keeps a C++ library out of the trusted path.

## 5. Configuration

Layered, lowest priority first: defaults → `config.json` (written by the snap configure
hook) → `self-config.json` (written by the client) → `VISIONTAK_*` environment.

The split matters. The hook regenerates `config.json` from snapd's configuration, so
anything the client wrote there is discarded the next time the hook runs — which
produced a device that registered successfully and then returned to the setup screen
with its address gone.

| Setting | Default | Notes |
|---|---|---|
| `server-url` | — | Asked for on screen if unset |
| `api-token` | — | Issued by the server at approval |
| `device-id` | generated | `visiontak_client_<uuid>`, stable across reboots |
| `cec-backend` | `auto` | `auto` \| `kernel` \| `libcec` \| `none` |
| `cec-device` | `/dev/cec0` | |
| `osd-name` | `VisionTAK` | Max 14 bytes — CEC spec limit |
| `refresh-interval` | `300` | Seconds; 0 disables |
| `rotate-interval` | `0` | Carousel; 0 disables |
| `start-dashboard` | — | Overrides the server's default |
| `allowed-hosts` | `*` | Navigation allowlist |
| `max-live-views` | `3` | Auto-tuned by board memory |
| `hardware-acceleration` | `auto` | `auto` \| `always` \| `never` |
| `verify-tls` | `true` | |
| `dhcp-discovery` | `false` | See below |

## 6. Control surface

| Remote | Keyboard | Action |
|---|---|---|
| OK / Enter | `Return`, `Space` | Open chooser, confirm |
| Up / Down | arrows | Open chooser, move |
| Left / Right, Ch +/− | arrows | Previous / next dashboard |
| 0–9 | `0`–`9` | Jump to Nth dashboard |
| Exit | `Escape` | Close chooser |
| Red | `m` | Toggle chooser |
| Green | `r`, `F5` | Reload |
| Yellow | `p` | Toggle carousel |
| Blue / Info | `i` | Diagnostics overlay |
| Power off/on | — | Blank / unblank |

Keys are captured before the WebView sees them. Bubbling gave the focused widget first
refusal, and once a dashboard is showing that is a WebView which swallows arrows,
digits and letters as page input — so every kiosk key silently did nothing.

## 7. The image

`image/build-image.sh` clones `canonical/pi-gadget`, patches it, signs a model, and runs
`ubuntu-image`. The result is `visiontak_client.img.xz`, roughly 1 GB compressed and
about 11.2 GB written.

Seeded snaps: `pi` (gadget), `pi-kernel`, `core24`, `snapd`, `bare`, `mesa-2404`,
`gnome-46-2404`, `gtk-common-themes`, `ubuntu-frame`, `visiontak-client`.

Partitions: `ubuntu-seed` 2500M, `ubuntu-boot` 750M, `ubuntu-save` 32M, `ubuntu-data`
8G. None of these grow later — upstream's `# XXX: make auto-grow to partition` is still
open — so what is set at build time is what the device lives with.

Gadget modifications:

- **Boot splash** — the logo padded to 800×400 at `splash/vendor-logo.png`
- **Full KMS** per board, with `vt.handoff=2` removed so plymouth owns the console
- **Pi 4 display** — `hdmi_group=1`, `hdmi_mode=16`, `hdmi_force_hotplug=1`
- **Pi 4 performance** — CMA 256M, `arm_boost=1`, `boot_delay=0`
- **`hdmi-cec` custom-device slot** in the gadget's *snapcraft.yaml*, with the matching
  `connections:` entry in *gadget.yaml*
- **Defaults** — `console-conf` disabled, `ubuntu-frame` set to run as a daemon
- **System-user assertion** — key-only SSH as `visiontak`

CI builds this on `ubuntu-24.04-arm` runners. Cross-building is impossible: the gnome
extension needs build-snaps matching the *target* architecture, and snapd cannot install
arm64 snaps into an amd64 container.

## 8. Known open issues

Stated plainly because they shape the recommendations below.

- **Pi 4 does not complete first-boot install.** The same card completes on a Pi 3 B+
  and then runs. Unresolved; no console has been attached to find out why.
- **CEC does not work.** `/dev/cec0` opens with `EPERM`, meaning the `custom-device`
  slot is not connected. This slot has now shipped broken twice, in two different ways,
  each discovered only on hardware.
- **DHCP option 225 discovery is off.** It cannot be made to work on Ubuntu Core:
  netplan cannot express `RequestOptions`, `--cloud-init` is rejected on UC20+, and
  gadget `cloud.conf` is installed but never runs because Core disables cloud-init when
  a device seeds without a datasource.
- **The UI layer has no test coverage.** `kiosk_window.py` is the largest file in the
  project and the test environment has no `gi`, so it is never imported under test.
  A crash in its constructor shipped to hardware with all tests green.

---

## 9. If starting over

Nothing above is wrong in its fundamentals. Ubuntu Core, strict confinement, Wayland
and kernel CEC are all still the right calls, and the client itself — configuration,
enrolment, rendering, CEC decoding — is well covered by 175 tests and was rarely the
problem.

**Essentially all the pain came from the image, and from the length of the feedback
loop around it.** A single mistake in a gadget YAML file costs a 25-minute CI build, a
card burn, a walk to the hardware and a boot — and most such mistakes are silent, with
the build going green and the fault appearing only as a device that does nothing. That
is what a second attempt should attack.

### Fix the feedback loop before writing any features

**Attach a serial console on day one.** A $10 USB-TTL adapter on the GPIO header would
have answered the Pi 4 install question immediately, and probably three others. Every
hour spent inferring from artifact sizes and commit timing was an hour that a serial
log would have ended in a minute. This is the single highest-value change.

**Test the gadget offline.** Every CEC failure was a gadget validation error that snapd
reports only at install time, as a `snap warnings` entry nobody reads. Build the gadget
and validate it in CI — even a scripted check of the slot invariants catches this class
of bug where it is cheap.

**Make `snap warnings` visible.** It is where snapd puts exactly the errors that matter
here, and it is invisible unless you already suspect something. The client should read
it and surface it in the diagnostics overlay.

**Have the device say what is wrong, honestly.** The panel reported
`KernelCecBackend` and `/dev/cec0 (present)` for an adapter that had never once opened,
because the status was the class name chosen from a bare `os.path.exists()`. That false
signal cost more than the underlying bug. Report observed state, never intent.

### Pin everything

**Pin snap revisions in the model, not channels.** Every snap currently tracks
`latest/stable` or `24/stable`, so two builds of the same commit can seed different
software. That makes "it worked yesterday" unreproducible and unbisectable, and it
means an upstream regression is indistinguishable from your own. Pin revisions, and
bump them deliberately.

**Ship the manifest with the image.** Already added; it should have existed from the
first build, so any two cards can be diffed.

### Own the gadget properly

The build script patches a freshly cloned upstream gadget with `sed` and `awk` on every
run — rewriting `config.txt` with conditional filters, inserting YAML blocks, resizing
partitions by matching literal strings. It works, but it is a program that edits another
program's source at build time, and it fails silently when upstream shifts.

**Fork the gadget into a real repository.** Commit the changes as changes. They become
reviewable, diffable and testable, upstream updates become explicit merges, and the
build script goes back to being a build script.

### Decide the hardware matrix up front

Pi 3 and Pi 4 disagree about the display stack, and supporting both meant per-model
`config.txt` filters, two CMA values, two driver choices, and a Pi 4 that still does not
install. **Pick one board.** If both are genuinely required, get both booting before
anything else is built, because retrofitting the second one costs more than it looks.

### Decide whether CEC is actually required

CEC has been by a wide margin the most expensive feature: a custom gadget slot, a fork
of the gadget, full KMS, per-board display configuration, and repeated silent
validation failures — and it still does not work. The kernel CEC code itself is fine and
well tested; the cost is entirely in getting the device node reachable from inside
confinement.

**If a cheap USB remote or a wall tablet is acceptable, take it.** If CEC is genuinely
required, budget it as a project rather than a feature, and prove the slot connects on
real hardware before building anything on top of it.

### Keep the client's own good decisions

These earned their place and should carry forward unchanged:

- Stdlib-only runtime, strict confinement, Wayland-only with no X11 fallback
- Board detection driving memory-sensitive defaults, since field units cannot be tuned
- The cached config, so a server outage does not blank the screen
- Registration polling that survives reboots, since approval can be days away
- The on-screen setup screen — the one thing that genuinely cannot be defaulted
- `probe.py`, which checks a server from a device's point of view with no device
- Treating the configure hook and the client as separate writers with separate files

### Test what ships

Add a CI job that installs `python3-gi`, `gir1.2-gtk-4.0` and `gir1.2-webkit-6.0` and
constructs the window headless under Xvfb. It only has to prove the thing starts.
The one file with no coverage is the one that puts pixels on the display, and a crash in
its constructor is indistinguishable, from the sofa, from a dead device.
