# Which Raspberry Pi the image runs on

One image, all supported boards. The `pi` gadget is universal across the Pi 2B, 3B,
3A+, 3B+, 4B, CM3 and CM4, and the snap is built for arm64, so the same
`visiontak_client.img.xz` boots any of them. Nothing needs rebuilding to move a card
between a Pi 3 and a Pi 4.

The Pi 5 is **not** covered — that gadget branch does not list it and it boots
differently. That is a separate piece of work.

## What differs by board

| | Pi 3 B+ | Pi 4 |
|---|---|---|
| RAM | 1 GB shared with the GPU | 2–8 GB |
| GPU | VideoCore IV, GL ES 2.0 | VideoCore VI, GL ES 3.1 |
| CPU | 4× A53 @ 1.4 GHz | 4× A72 @ 1.5 GHz |
| `max-live-views` | 1 (automatic) | 3 |
| `hardware-acceleration` | `never` (automatic) | `auto` |

The client reads `MemTotal` at start and applies the low-memory profile below 1536 MiB,
so a Pi 3 B+ tunes itself down and a 2 GB+ Pi 4 keeps the full defaults. Nobody has to
configure a field unit by hand — which matters, because a field unit has no login by
default.

An original **1 GB Pi 4** trips the same threshold and gets the Pi 3 profile. That is
intended, and worth knowing if one behaves more conservatively than you expect.

The gadget already carries Pi 4 boot settings: `max_framebuffers=2`, which full KMS
needs for dual HDMI, and `arm_boost=1`.

## CMA

The gadget allocates 128 MB of contiguous memory to the GPU for every model. That suits
a Pi 3 B+, where 256 MB would be a quarter of the board's entire memory, and is modest
for a Pi 4 driving a large panel with video:

```sh
CMA_MB=256 image/build-image.sh dist/visiontak-client_0.1.0_arm64.snap
```

It is a build-wide setting rather than per-model on purpose. `config.txt` filters would
need a separate `dtoverlay=vc4-kms-v3d` line per model, and any board nobody thought to
list would then load no display overlay at all — a far worse failure than a
conservative default.

## Heavy dashboards

Measured in the test VM on a dashboard embedding a continuously animating map, the Pi 3
profile took CPU from a runaway 551% to a settling 92%. A Pi 4's real GPU and extra
memory make that class of dashboard comfortable rather than marginal, so put the
embed-heavy displays on Pi 4s and keep Pi 3 B+ units for dashboards the server renders.

Four simultaneous video tiles is a lot to ask of a Pi 3 B+ even with the decoders
present. If they play but stutter, that is the board, not the configuration.

## If a board has no video output

The display driver is the usual cause, and it can be changed on the card without
rebuilding anything. `ubuntu-seed` is a FAT partition, so it mounts on Windows, macOS
or Linux when you put the SD card in a reader. Edit `config.txt`:

| Line | Effect |
|---|---|
| `dtoverlay=vc4-kms-v3d,cma-128` | Full KMS. `/dev/cec0` exists, so the remote works |
| `dtoverlay=vc4-fkms-v3d,cma-128` | Fake KMS. Video only — no CEC adapter is registered |

Fake KMS inherits the framebuffer the firmware already set up. Full KMS makes the
kernel detect the panel itself, which is where a display behind a switch, on a long
cable, or not powered at boot can end up with no output. `hdmi_force_hotplug=1` is set
for the Pi 4 to drive HDMI regardless of what it reads back; if a display still shows
nothing, fake KMS is the fallback and costs only the remote.

The same choice at build time:

```sh
DISPLAY_DRIVER=fkms image/build-image.sh …
```

Worth checking before changing anything: on a Pi 4, **HDMI0 is the port nearest the
USB-C power socket**. Full KMS is stricter than fake KMS about which output it lights
up, so a cable in HDMI1 is worth moving before blaming the driver.

### The Pi 4 needs its HDMI mode stated

A Pi 4 under full KMS showed no picture at all until `config.txt` named the mode:

```
[pi4]
hdmi_force_hotplug=1
hdmi_group=1
hdmi_mode=16
[all]
```

Fake KMS inherited whatever the firmware had already negotiated; full KMS makes the
kernel do it, and on a Pi 4 that produced nothing. Forcing hotplug alone was not
enough — the mode had to be stated outright. Group 1 mode 16 is CEA 1080p60, which is
what a television is.

With that in place the Pi 4 runs full KMS, so it keeps `/dev/cec0` and the remote. A
panel that wants something other than 1080p60 needs different values:

```sh
HDMI_GROUP=1 HDMI_MODE=4 image/build-image.sh …   # 720p60
PI4_DRIVER=fkms image/build-image.sh …            # give up CEC, take any mode
```

The mode tables are in the Raspberry Pi `config.txt` documentation.
