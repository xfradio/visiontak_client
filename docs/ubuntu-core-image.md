# Building the Ubuntu Core image

Target: Raspberry Pi 3 B+/4/5, Ubuntu Core 24, strict confinement, Wayland only.

The boot splash is a file drop in this same gadget fork — see
[`boot-splash.md`](boot-splash.md). Pi 3 B+ tuning is in
[`raspberry-pi-3.md`](raspberry-pi-3.md); the defaults here assume a Pi 4 or 5.

## Snap set

| Snap | Role |
|---|---|
| `pi` (gadget) | Boot assets **and** the `custom-device` slot that exposes `/dev/cec0` |
| `pi-kernel` | Kernel; must have `CONFIG_DRM_VC4` + `CONFIG_CEC_CORE` (the stock kernel does) |
| `core24` | Base |
| `snapd` | — |
| `ubuntu-frame` | Mir/Wayland compositor, the only thing that touches the display |
| `visiontak-client` | This project |

## 1. Fork the gadget to publish the CEC device

`/dev/cec0` is not covered by any built-in snapd interface. Rather than dropping the
snap to `confinement: classic` — which would discard every guarantee we picked Ubuntu
Core for — the gadget declares a `custom-device` slot. Access then becomes a property
of the signed image, visible in `snap connections`, revocable with `snap disconnect`.

Clone the Pi gadget and add the slot to its **`snapcraft.yaml`**, alongside the GPIO
and I2C slots it already declares. Snap slots live in `snapcraft.yaml`, which becomes
`meta/snap.yaml`; `gadget.yaml` accepts a `slots:` block, ignores it, and leaves you
with `snap "pi" has no slot named "hdmi-cec"` on the device:

```yaml
slots:
  hdmi-cec:
    interface: custom-device
    custom-device: hdmi-cec
    devices:
      - /dev/cec0
      - /dev/cec1
    udev-tagging:
      - kernel: cec[0-9]
        subsystem: cec
```

Build and sign it:

```bash
snapcraft --build-for=arm64 --use-lxd
snap sign -k <your-key> gadget.assert   # required for a non-dangerous image
```

> `custom-device` requires snapd 2.60+ and the connection must be authorised by a
> store assertion or `--dangerous` on a development image.

## 2. Model assertion

`visiontak-model.json`:

```json
{
  "type": "model",
  "series": "16",
  "authority-id": "<your-brand-account-id>",
  "brand-id": "<your-brand-account-id>",
  "model": "visiontak-pi-arm64",
  "architecture": "arm64",
  "base": "core24",
  "grade": "signed",
  "snaps": [
    { "name": "pi",         "type": "gadget", "default-channel": "24/stable" },
    { "name": "pi-kernel",  "type": "kernel", "default-channel": "24/stable" },
    { "name": "core24",     "type": "base",   "default-channel": "latest/stable" },
    { "name": "snapd",      "type": "snapd",  "default-channel": "latest/stable" },
    { "name": "ubuntu-frame", "type": "app",  "default-channel": "22/stable" },
    { "name": "visiontak-client", "type": "app", "default-channel": "latest/stable" }
  ],
  "timestamp": "2026-08-07T00:00:00+00:00"
}
```

```bash
snap sign -k <your-key> visiontak-model.json > visiontak.model
ubuntu-image snap visiontak.model -O ./out \
  --snap ./pi_*.snap --snap ./visiontak-client_*.snap
```

## 3. First boot configuration

Ship these as a gadget `defaults` block so a field unit needs no console at all:

```yaml
defaults:
  <visiontak-client-snap-id>:
    server-url: https://visiontak.example
    refresh-interval: 300
    osd-name: VisionTAK
  <ubuntu-frame-snap-id>:
    daemon: true
    cursor: none
    idle-timeout: 0          # a wall display must never blank itself
```

`cursor: none` matters: there is no mouse in the field, and a stray pointer parked in
the middle of a dashboard is the most common visual defect on kiosk deployments.

## 4. Interface connections

Store-published snaps get `wayland`, `opengl`, `network`, `network-bind` and
`browser-support` auto-connected. The two to verify:

```bash
snap connections visiontak-client
sudo snap connect visiontak-client:hdmi-cec pi:hdmi-cec   # if not auto-connected
```

`hdmi-cec` should read `pi:hdmi-cec`. On an image from `image/build-image.sh` it is
connected on first boot by cloud-init, not by snapd's own auto-connection — see below —
so it is listed as `manual`. That is expected here, and it does survive a reboot; what
does not survive is a connection someone made by hand on a card that will be reflashed.

### Why `gadget.yaml: connections:` is not used

It is the documented way to connect an interface at first boot, and it cannot work on a
locally built image. From snapd, `overlord/ifacestate/helpers.go`,
`addGadgetConnections`:

```go
snapID := snapInfo.SnapID
if snapID == "" {
    // not a snap-id identifiable snap, skip
    return nil
}
```

A snap-id comes from a snap-declaration assertion, which only the store issues. The
client and the `pi` gadget are both built locally and unasserted, so they have none and
the entire stanza is skipped — no Connect task, not a failed one, no warning, nothing in
`snap tasks 1`. Every other connection in that seed produces a task because those are
store snaps.

Past that check it fails again: entries are matched against the real snap-id, and the
other side goes through `resolveSnapIDToName`, which returns `""` for a snap with no
declaration, so `slot: pi:hdmi-cec` resolves to a nil slot and is dropped with
`gadget connections: ignoring missing slot`. Snap *names* in the snap-id position parse,
but they never resolve. Three spellings of this stanza shipped before that was clear.

To use this route, publish both snaps (a brand store counts) and write their actual
snap-ids into `gadget.yaml`.

### What connects it instead

`image/gadget-cloud.conf` is shipped as the gadget's `cloud.conf`, which snapd installs
at first boot as `/etc/cloud/cloud.cfg.d/80_device_gadget.cfg` — grade `dangerous` takes
it unfiltered. Its `runcmd` waits for seeding and runs the `snap connect`.

The datasource block in it is load-bearing. An earlier attempt shipped the config alone
and nothing ran: with no datasource to find, cloud-init reports itself untriggered and
Ubuntu Core writes `/etc/cloud/cloud-init.disabled` before any module executes. The
`None` datasource is cloud-init's documented answer to config that is already on disk.

Check it after a flash:

```bash
cloud-init status --long                  # done, not disabled
sudo cat /var/log/cloud-init-output.log | grep -A2 'snap connect'
```

`x11`, `desktop-legacy`, `desktop` and `gsettings` **do** appear in that list: the
`gnome` extension declares them on every app it wraps and a snap cannot subtract them.
What matters is that they are *unconnected* — Ubuntu Core provides no slot for them, so
no rule reaches the profile. Verify rather than assume:

```bash
snap connections visiontak-client | awk '$3 == "-"'          # x11 etc. must be here
grep -c '/tmp/.X11-unix' /var/lib/snapd/apparmor/profiles/snap.visiontak-client.daemon
```

The second command must print `0`. There should still be no `home` or `system-files`
at all.

Check what `hdmi-cec` is wired to, not merely that it is wired. On a development
install snapd has been observed auto-connecting it to `console-conf:terminal-devices`,
whose `custom-device` attribute is `terminal-control` rather than `hdmi-cec` — that
grants `/dev/tty[0-9]` and `/dev/ttyS[0-9]` `rwk` and no CEC at all:

```bash
snap connections visiontak-client | grep hdmi-cec   # slot must be the gadget's
grep -nE '/dev/tty|/dev/cec' /var/lib/snapd/apparmor/profiles/snap.visiontak-client.daemon
```

If the slot is anything but the gadget's `hdmi-cec`, `snap disconnect` it.

## 5. Development shortcut

For bring-up before the gadget is forked, install with `--devmode` on a Pi running
Ubuntu Server + `ubuntu-frame`. This makes `/dev/cec0` reachable without the gadget
slot. Do not ship `--devmode` — it disables confinement entirely.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `no Wayland compositor after 60s` | `ubuntu-frame` not running, or `XDG_RUNTIME_DIR` isn't `/run/user/0` |
| Black screen, no logs | Frame started after us and we exited; the daemon restarts every 5s — check `snap logs -f visiontak-client` |
| `cannot open /dev/cec0` | `hdmi-cec` interface not connected, or the kernel has no CEC adapter (`ls /dev/cec*`) |
| `no physical address yet` | TV is off or the EDID has not been read; the client re-claims automatically on hot-plug |
| Dashboards blank but no error | Navigation blocked by the host allowlist — check `snap logs` for `blocked navigation to` |

### The slot has to validate, or it does not exist

An invalid `custom-device` slot is not rejected at build time. snapcraft packs it,
`ubuntu-image` accepts it, the device boots — and snapd then refuses to publish the
slot, leaving the plug unconnected with no visible cause. `snap connections` shows a
bare `-` and nothing explains it.

```
$ snap warnings
warning: snap "pi" has bad plugs or slots: hdmi-cec (custom-device "read-devices"
  path must start with /dev/ and cannot contain special characters:
  "/sys/devices/platform/soc/*.hdmi/cec*")
```

`read-devices` accepts device nodes under `/dev/` only — a sysfs path invalidates the
whole slot. The client reaches CEC entirely through ioctls on `/dev/cec0`, so it needs
no sysfs access and the entry is simply gone.

**`snap warnings` is the command that finds this.** Nothing in `snap connections`,
`snap interfaces` or the journal points at it.
