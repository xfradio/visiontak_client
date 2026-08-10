# HDMI-CEC

## Why the kernel API and not libcec

On a Pi the vc4 DRM driver already implements a full CEC adapter and exposes it as
`/dev/cec0`. libcec would sit on top of that same device and add a C++ dependency, a
larger snap, and a second copy of the state machine. `src/visiontak_client/cec/kernel.py`
talks to the ioctls directly with `ctypes` — no compiled extension, no vendored library.

`libcec.py` is kept for x86 deployments with a Pulse-Eight USB dongle, where there is
no kernel adapter to talk to. `cec_backend=auto` picks the kernel adapter when
`/dev/cec0` exists and falls back to libcec, then to a no-op backend that keeps the
display running without a remote.

## What we register as

A **playback device** in *plain follower* mode
(`CEC_MODE_INITIATOR | CEC_MODE_FOLLOWER`). Playback is the right device type for
something the TV should be able to switch to as a source, and plain follower means the
kernel answers the mandatory core messages — physical address, OSD name, vendor id,
CEC version, power status — on our behalf. We only handle:

| Opcode | Handling |
|---|---|
| `0x44` USER_CONTROL_PRESSED | Mapped through `keymap.py` to an action |
| `0x45` USER_CONTROL_RELEASED | Ends key repeat |
| `0x36` STANDBY | Blank the surface |
| `0x04/0x0D` IMAGE/TEXT_VIEW_ON | Unblank |
| `0x86` SET_STREAM_PATH | If the path is ours, re-announce as active source |
| `0x82` ACTIVE_SOURCE | If it is not us, note that we lost the input |

At start-up and on every HDMI hot-plug we send `IMAGE_VIEW_ON` to the TV followed by a
broadcast `ACTIVE_SOURCE`, so a display that boots with the wrong input selected
switches itself to the kiosk.

## Button map

| Remote button | CEC code | Action |
|---|---|---|
| ▲ / ▼ | `0x01` / `0x02` | Open chooser, move selection |
| ◀ / ▶ | `0x03` / `0x04` | Previous / next dashboard (or move in the chooser) |
| OK | `0x00`, `0x2B` | Open chooser / confirm |
| Back, Exit | `0x0D` | Close chooser |
| Menu, Settings | `0x09`, `0x0A`, `0x0B` | Toggle chooser |
| 1–9 | `0x21`–`0x29` | Jump to that dashboard |
| 0 | `0x20` | Jump to the tenth |
| Ch+ / Ch− | `0x30` / `0x31` | Next / previous dashboard |
| ⏩ / ⏪ | `0x49` / `0x48` | Next / previous dashboard |
| ⏹ | `0x45` | Back to the first dashboard |
| Red | `0x72` | Toggle chooser |
| Green | `0x73` | Reload the current dashboard |
| Yellow | `0x74` | Pause / resume the carousel |
| Blue | `0x71` | Toggle the diagnostics panel |

The map is deliberately generous — remotes differ wildly in which subset of the CEC UI
command table they emit, and a button that does nothing reads as a broken appliance.
Unmapped codes are logged at DEBUG and dropped.

## Bring-up without a remote

The same actions are bound to keyboard keys (`keymap.KEYBOARD_MAP`), so a USB keyboard
drives the kiosk before CEC is wired: arrows, Enter, Escape, `m` (menu), `r` (reload),
`i` (info), `p` (pause carousel), digits.

## Debugging on device

```bash
# Is there an adapter at all?
ls -l /dev/cec*

# Watch raw traffic (cec-utils, on a dev image only)
cec-ctl -d0 --playback --osd-name VisionTAK
cec-ctl -d0 --monitor

# What the client sees
snap logs -f visiontak-client
sudo snap set visiontak-client verbose=true   # or run the binary with --verbose
```

Common findings:

- **No traffic at all** — CEC is off in the TV's menu. Every vendor names it something
  else: Anynet+ (Samsung), Bravia Sync (Sony), Simplink (LG), Viera Link (Panasonic).
- **Key presses arrive only while the kiosk is the active source** — expected; that is
  how CEC routes the remote. Press the TV's source button to hand control back.
- **`no physical address yet`** — the TV is off or the EDID has not been read. The
  client re-claims its logical address on the next state-change event, no restart needed.
- **Long HDMI runs / switches** drop CEC first. If the picture is fine but CEC is
  flaky, suspect the cable or an intermediate splitter that does not pass CEC through.
