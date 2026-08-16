# Boot splash

The logo appears in three places, at three different layers. They are configured
independently and it is worth knowing which one you are looking at when something
looks wrong.

| When | Layer | Configured in |
|---|---|---|
| From early boot, before anything else | Plymouth, driven by the gadget | `splash/vendor-logo.png` in the forked gadget |
| Once Ubuntu Frame owns the display | Frame's background | `snap set ubuntu-frame` |
| While a dashboard is connecting | This client | `assets/visiontak-logo.png`, staged into the snap |

## 1. Early boot — the gadget

Ubuntu Core 22 and later render a splash with Plymouth. You do **not** need to author a
Plymouth theme or rebuild the kernel snap: dropping a PNG into the gadget is enough.
The Pi gadget already enables the splash, so this is the only change needed.

> **This layer does not work on the images this repo builds.** They use full KMS for
> CEC, and Plymouth cannot start under it — see
> [the splash and CEC cannot both work](#the-splash-and-cec-cannot-both-work--cec-wins)
> at the end. The rest of this section applies to a `DISPLAY_DRIVER=fkms` build.

Put the image at the root of the gadget snap:

```
splash/vendor-logo.png
```

The file name is fixed. A 2:1 aspect ratio renders best — 800×400 is a good default —
so the square master needs padding rather than scaling:

```sh
make splash          # writes build/vendor-logo.png from assets/visiontak-logo.png
```

That is ImageMagick doing:

```sh
convert assets/visiontak-logo.png \
  -resize 400x400 -background black -gravity center -extent 800x400 \
  build/vendor-logo.png
```

Padding on black rather than stretching to 2:1 matters here — the logo is a hexagon,
and a non-uniform scale is immediately obvious on a wall display.

Copy it into the gadget tree next to the `hdmi-cec` slot work from
[`ubuntu-core-image.md`](ubuntu-core-image.md), then rebuild and re-sign the gadget:

```sh
cp build/vendor-logo.png <pi-gadget>/splash/vendor-logo.png
snapcraft --build-for=arm64 --use-lxd
```

To turn the splash off entirely, remove `splash` and `vt.handoff=2` from
`configs/cmdline.txt` in the gadget.

If you want more than a static logo — progress, animation — Plymouth's **Scripts**
plugin is the only one Ubuntu Core supports; the other Plymouth plugins will not work.

## 2. Ubuntu Frame's background

Between Plymouth handing off and the client's first paint there is a window where
Frame owns an empty display. Match it to the logo's field so the transition is not a
flash of a different colour:

```sh
sudo snap set ubuntu-frame daemon=true cursor=none idle-timeout=0
```

`vm/deploy.sh` already applies these in the test VM.

## 3. The client's own splash

Shipped in this snap and requires no image work: `assets/visiontak-logo.png` is staged
by the `branding` part and shown by `_build_splash()` in `ui/kiosk_window.py`, both
before any dashboard exists and over each webview until its first paint.

If the file is missing the client logs it and falls back to a text splash — a display
still comes up, which matters more in the field than the artwork.

On a Pi 3 the loading splash is doing real work rather than decoration: WebKit can take
several seconds to first paint there, and without the cover you get a white flash and a
half-drawn dashboard.

## Sizing

Ship a square master of at least 512×512 in `assets/`. Do not ship a 4K PNG and rely on
scaling — a Cortex-A53 decodes it on every splash, and the Pi 3's framebuffer tops out
at 1920×1080.

## The splash and CEC cannot both work — CEC wins

**Under full KMS there is no plymouth splash, and this build cannot give you one.**
Layer 1 above therefore only applies to a fake-KMS image.

CEC needs full KMS: under fake KMS the firmware owns the display and Linux registers
no CEC adapter, so `/dev/cec0` never exists. Switching to `vc4-kms-v3d` for that
reason cost the boot splash, and the screen showed the text console instead.

That was first blamed on `vt.handoff=2` in the gadget's `cmdline.txt`, on the reasoning
that it defers the console to a firmware framebuffer full KMS never creates. The build
removed it. **That was wrong** — removing it changed nothing, because the handoff was
never the cause.

The actual cause: plymouth lives in the **pi-kernel snap's initramfs** and needs a DRM
device at the moment it starts. Under full KMS that device comes from `vc4`, which is
not in the initramfs, so plymouth never takes the display at all.

The distinguishing symptom is worth knowing, because it tells you which problem you
have without any further digging:

| On screen at boot | Means |
|---|---|
| Raw text console | Plymouth never started — no DRM device. This is full KMS. |
| Ubuntu's own logo | Plymouth is running but did not find `splash/vendor-logo.png`. |
| VisionTAK hexagon | Working. |

The kernel snap is upstream of this build, so the two cannot be reconciled here. CEC is
the entire point of the appliance, so the splash gives way. What the build does instead
is make the failure quiet rather than ugly — a wall display scrolling kernel messages
reads as a broken computer, where black reads as a device starting up:

- `console=tty1` → `console=tty3`, which is where the messages belong
- `loglevel=0`, because `quiet` still lets warnings and errors through
- `vt.global_cursor_default=0`, or a blinking block is the only thing on screen
- `systemd.show_status=false`, which is the one that empties the screen

Moving the console to tty3 does **not** move it off the panel, and believing it did
cost a card. The kernel makes the last `console=ttyN` the foreground VT, so the panel
shows tty3 instead of tty1 and the text is exactly as visible as before. Nor do `quiet`
and `loglevel=0` reach it: `[ OK ] Started …` and the first-boot install progress are
written by systemd to `/dev/console` directly rather than through printk, so they
survive every printk-level setting. `systemd.show_status=false` is what silences them.

The other half is what is *running* on that VT, which no kernel parameter touches:

```bash
cat /sys/class/tty/tty0/active                          # the VT on screen
systemctl list-units --all 'getty@*' 'console-conf@*'   # what is drawing on it
```

On a working unit `tty0/active` reads `tty4` — Ubuntu Frame's own VT, which it takes
when it starts. Before that the display is on a text VT, and both candidates have
something drawing on them: `console-conf@tty3` on the console VT, `getty@tty1` on VT1.
`console-conf: disable: true` in the gadget defaults does not prevent the first of
those; it skips the setup wizard and leaves a login prompt on the console VT, which is
text on a television for as long as boot takes.

Which of the two is actually in front depends on whether the kernel moves the display
along with `console=tty3`, so `image/gadget-cloud.conf` clears both rather than betting
on it: `console-conf@tty1`, `console-conf@tty3`, `getty@tty1` and `getty@tty3` are all
masked on first boot.

The keyboard login is moved, not given up — the same file enables `getty@tty2`, on a VT
nothing displays. **Alt-F2** on a unit whose network is down.

Because the masking is done by cloud-init rather than baked into the image, the very
first boot still shows the prompt until cloud-final runs. Every boot after that is
black. Baking it in would need a writable rootfs at build time, which a UC24 image does
not have.

The boot is then black until Ubuntu Frame comes up, and the branding arrives with the
client's own splash, which is held for `_SPLASH_MIN_SECONDS` so it is actually seen.

If you would rather have the boot splash than the remote, the trade is explicit:

```sh
DISPLAY_DRIVER=fkms image/build-image.sh …   # splash back, no CEC, dead remote
```

The client's own logo splash is unaffected either way — it renders under Ubuntu Frame,
long after any of this.
