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
