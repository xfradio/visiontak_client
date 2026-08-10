# Building the SD card image in CI

The `image` job in `.github/workflows/build.yml` produces a bootable
`.img.xz` for the Raspberry Pi with the client, Ubuntu Frame and your boot splash baked
in. Burn it, boot it, done — no `console-conf`, no manual `snap install`.

It is skipped until you complete the one-time setup below, because **model assertions
are always signed** and only you can create the key.

## Why signing is unavoidable

Ubuntu Core boots from a model assertion, and snapd verifies its signature against your
brand account before trusting the image. There is no unsigned path, not even for
development: `grade: dangerous` waives the requirement that the *snaps* be
store-signed, not that the *model* be signed.

So the key must be created by you, registered against your Ubuntu One account, and
handed to CI as a secret.

## One-time setup

On any Linux box with snapcraft (the WSL distro is fine):

```sh
snapcraft login                     # the Ubuntu One account used for the device
snapcraft create-key visiontak
snapcraft register-key visiontak
snapcraft whoami                    # note the account-id
```

`register-key` is the step that makes the signature verifiable on the device. A key that
is created but not registered produces an image that will not boot.

Export the private key:

```sh
GNUPGHOME=~/.snap/gnupg gpg --armor --export-secret-keys visiontak
```

Then in the repository settings:

| Kind | Name | Value |
|---|---|---|
| Secret | `MODEL_SIGN_KEY` | the full armored block, `-----BEGIN` to `-----END` |
| Variable | `BRAND_ID` | the account-id from `snapcraft whoami` |
| Variable | `MODEL_KEY` | `visiontak` |

`BRAND_ID` and `MODEL_KEY` are *variables*, not secrets — they are not sensitive and
keeping them visible makes failures readable. The job keys off `vars.BRAND_ID`, so it
stays skipped until you set it.

> The repository is public. `MODEL_SIGN_KEY` is a real private key: GitHub will not
> expose it to workflows triggered by pull requests from forks, and the job excludes
> `pull_request` events for that reason. Anyone who obtains it can sign images that
> claim to be yours — revoke and re-register if it ever leaks.

## What the job builds

1. Pads `assets/visiontak-logo.png` to 800×400 on black — the splash wants 2:1, and
   stretching a hexagon is obvious on a wall display.
2. Clones `canonical/pi-gadget` branch `24` and drops the logo at
   `splash/vendor-logo.png`. The Pi gadget already enables the splash (`splash` and
   `vt.handoff=2` are in `configs/cmdline.txt`), so supplying the file is the whole
   change.
3. Appends the `hdmi-cec` `custom-device` slot to `gadget.yaml`, so CEC is an auditable
   property of the image rather than a reason to drop confinement.
4. Builds the gadget, signs the model, and runs `ubuntu-image`.

Output lands as the **`visiontak-pi-sdcard-image`** artifact.

## Burning it

Raspberry Pi Imager (*Use custom*) or:

```sh
xzcat visiontak-pi-arm64.img.xz | sudo dd of=/dev/sdX bs=4M status=progress conv=fsync
```

Check `lsblk` first — `dd` to the wrong device takes your disk with it.

## First boot

The image is `grade: dangerous`, so it still runs `console-conf` unless you add a
system-user assertion. To skip the console entirely, ship device settings as gadget
`defaults` (see [`ubuntu-core-image.md`](ubuntu-core-image.md)) and add a system-user
assertion signed with the same key.

Confirm CEC came through:

```sh
snap connections visiontak-client | grep hdmi-cec     # slot should be pi:hdmi-cec
ls /dev/cec*
```

If the slot is there, `cec-backend=auto` works and the TV remote drives the kiosk.

## Building it locally

Same script, on an arm64 host:

```sh
BRAND_ID=<account-id> MODEL_KEY=visiontak \
  image/build-image.sh dist/visiontak-client_0.1.0_arm64.snap
```

It refuses to run on x86 — the gadget cannot be cross-built for the same reason the
client cannot; see [`arm64-build.md`](arm64-build.md).
