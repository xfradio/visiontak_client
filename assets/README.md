# Brand assets

| File | Used by |
|---|---|
| `visiontak-logo.png` | Kiosk splash and per-dashboard loading overlay (staged into the snap), and the source for the gadget's boot splash |

`visiontak-logo.png` should be the square logo on a black field, at least 512×512.
The build downscales it; the Pi 3's framebuffer is 1920×1080 at most and decoding a
large PNG on a Cortex-A53 is slower than shipping a right-sized one.

The boot splash is generated from this file — see `docs/boot-splash.md`.
