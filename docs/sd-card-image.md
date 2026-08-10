# Bootable SD card for a Raspberry Pi

This is the stock-image route: burn Canonical's Ubuntu Core 24 image, then install this
client onto the running device. It needs no model signing and no gadget fork, so it
works today.

What it does not give you: HDMI-CEC (no gadget slot — the remote is dead, keyboard
works), the boot splash, and an unattended first boot. Those need the custom image in
[`ubuntu-core-image.md`](ubuntu-core-image.md) and [`boot-splash.md`](boot-splash.md).

## 1. Get the image

Ubuntu Core 24, **arm64** — the Pi 3 B+, 4 and 5 all run it:

<https://cdimage.ubuntu.com/ubuntu-core/24/stable/current/>

Take `ubuntu-core-24-arm64+raspi.img.xz`. Do not use the `amd64` image the test VM
uses; it will not boot on a Pi.

## 2. Burn it

Raspberry Pi Imager (*Use custom* → the `.img.xz`) or, on Linux:

```sh
xzcat ubuntu-core-24-arm64+raspi.img.xz | sudo dd of=/dev/sdX bs=4M status=progress conv=fsync
```

Check `lsblk` for the right device first — `dd` to the wrong one takes your disk with
it. On Windows, use Imager rather than hunting for a raw-write tool.

## 3. First boot

Ubuntu Core runs `console-conf` on first boot and **requires an Ubuntu One account with
an SSH key uploaded** to <https://login.ubuntu.com/ssh-keys>. Attach a keyboard and
monitor, or a USB-serial adapter to the GPIO header.

It prints the account name and address it configured:

```
ssh <user>@<device-ip>
```

The Pi needs a network route to the VisionTAK server, and the key must be on the
account *before* first boot — console-conf reads it once and does not re-check.

## 4. Install the kiosk

Copy the arm64 snap over and install it. `make snap` produces this; the amd64 build
from the test VM will not install here.

```sh
scp visiontak-client_*_arm64.snap <user>@<device>:/tmp/
ssh <user>@<device>
```

On the device:

```sh
sudo snap install ubuntu-frame
sudo snap set ubuntu-frame daemon=true cursor=none idle-timeout=0
sudo snap install --dangerous /tmp/visiontak-client_*_arm64.snap
```

`--dangerous` is required because the snap is not store-signed. It stays strictly
confined; the flag only waives the signature check.

## 5. Configure

```sh
sudo snap set visiontak-client \
  server-url=https://visiontak.example \
  api-token=… \
  cec-backend=none
```

`cec-backend=none` because a stock gadget publishes no `hdmi-cec` slot, so `/dev/cec0`
is unreachable. With `auto` the client logs a CEC failure on every start.

On a **Pi 3 B+** add the tuning from [`raspberry-pi-3.md`](raspberry-pi-3.md):

```sh
sudo snap set visiontak-client max-live-views=1 hardware-acceleration=never
```

If any dashboard embeds third-party content, it will render blank until you allow it:

```sh
sudo snap set visiontak-client allowed-hosts='*'
```

## 6. Check it

```sh
snap services visiontak-client
snap logs -f visiontak-client
```

A healthy start reaches `server allows N dashboard(s)`. Service state alone is not
evidence the kiosk works — the daemon can sit `active` with `NRestarts=0` while showing
nothing but the splash, so read the log rather than the service table.

Confirm the confinement is what you expect:

```sh
P=/var/lib/snapd/apparmor/profiles/snap.visiontak-client.daemon
grep -c '/tmp/.X11-unix' $P     # must be 0
snap connections visiontak-client | grep hdmi-cec
```

`x11` and `desktop-legacy` appear as *declared* plugs — the gnome extension adds them
to every snap it wraps and they cannot be removed. What matters is that they are
unconnected, which the first command proves.

## Moving to a real image

Once the device is doing what you want, the custom image bakes all of the above in —
snaps, config defaults, the CEC slot and the boot splash — so a field unit needs no
console at all. That is [`ubuntu-core-image.md`](ubuntu-core-image.md), and it needs a
signed model assertion.
