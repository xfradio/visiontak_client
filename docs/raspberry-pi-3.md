# Running on a Raspberry Pi 3 B+

The Pi 3 B+ is a materially smaller machine than the Pi 4/5 this client was first
built for, in two ways that both matter:

| | Pi 3 B+ | Pi 4 |
|---|---|---|
| RAM | **1 GiB, shared with the GPU** | 2–8 GiB |
| GPU | VideoCore IV, GL ES 2.0 | VideoCore VI, GL ES 3.1 |
| CPU | 4× Cortex-A53 @ 1.4 GHz | 4× Cortex-A72 @ 1.5 GHz |

RAM is the binding constraint. Ubuntu Core, Ubuntu Frame and one WebKit web process
already account for most of a gigabyte before a dashboard has drawn anything.

## Settings that matter

```sh
sudo snap set visiontak-client \
  max-live-views=1 \
  hardware-acceleration=never \
  refresh-interval=600
```

**`max-live-views=1`** is the important one. Each dashboard you visit keeps its own
WebKit web process alive, holding its render tree, timers and any animation the page
runs — a hidden dashboard is not an idle one. At the default of 3, a Pi 3 with three
dashboards will swap or hit the OOM killer. At 1, switching costs a reload; the splash
covers it. Raise it only if you have measured the headroom.

**`hardware-acceleration=never`** stops WebKit forcing accelerated compositing onto
VideoCore IV. On that part the accelerated path is frequently slower than the software
one and considerably less stable, and it competes for the same RAM the CPU needs. This
also disables WebGL, which on a Pi 3 falls back to a software rasteriser that costs far
more than it returns.

**`refresh-interval=600`** halves the config polling. It is a small win, but the
default of 300 s buys nothing on a display whose dashboard list rarely changes.

## What the settings are worth

Measured in the test VM (6 vCPU, so 600% is full saturation) on a dashboard embedding
a continuously animating third-party map — the worst case this client is likely to
meet:

| | CPU over 80 s | Guest responsive? |
|---|---|---|
| Defaults (`max-live-views=3`, acceleration forced on) | 483% → 551%, climbing | no — SSH timed out |
| Pi 3 profile above | 147% → 92%, settling | yes throughout |

The difference is mostly `hardware-acceleration=never`. Forcing accelerated
compositing onto a GPU that cannot service it does not degrade gracefully: it turns
into a compositing treadmill that consumes more the longer it runs. The software path
settles instead.

So heavy embedded content is not automatically disqualifying on a Pi 3 — but check it,
per dashboard, rather than assuming. `allowed-hosts` gates whether third-party content
loads at all (see the main README), so a restrictive allowlist is often why a heavy
embed is not costing you anything yet.

## Memory headroom

Check a running display rather than guessing:

```sh
free -m
snap logs -n 50 visiontak-client | grep evicting
```

`evicting webview for …` at every dashboard change is expected at `max-live-views=1`
and is the mechanism working, not a fault.

## Image notes

Ubuntu Core 24 arm64 runs on the Pi 3 B+. Use the same `pi` gadget fork as the Pi 4/5
build — the `hdmi-cec` `custom-device` slot and the boot splash are identical, and the
Pi 3's VideoCore IV is a CEC adapter in the same way.

Ship the tuning above as gadget `defaults` so a field unit needs no console:

```yaml
defaults:
  <visiontak-client-snap-id>:
    max-live-views: 1
    hardware-acceleration: never
```
