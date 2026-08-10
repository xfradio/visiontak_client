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
