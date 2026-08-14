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
| Secret | `MODEL_SIGN_PASSPHRASE` | the key's passphrase — omit only if it has none |
| Variable | `BRAND_ID` | the account-id from `snapcraft whoami` |
| Variable | `MODEL_KEY` | `visiontak` |

`snapcraft create-key` prompts for a passphrase, so most keys have one. `snap sign`
shells out to gpg with no passphrase arguments, so the only way it can work
unattended is if gpg-agent already holds the secret — the workflow presets it. Without
`MODEL_SIGN_PASSPHRASE` a protected key fails with *"Sorry, we are in batchmode - can't
get input"*.

`BRAND_ID` and `MODEL_KEY` are *variables*, not secrets — they are not sensitive and
keeping them visible makes failures readable. The job keys off `vars.BRAND_ID`, so it
stays skipped until you set it.

> The repository is public. `MODEL_SIGN_KEY` is a real private key: GitHub will not
> expose it to workflows triggered by pull requests from forks, and the job excludes
> `pull_request` events for that reason. Anyone who obtains it can sign images that
> claim to be yours — revoke and re-register if it ever leaks.

## Why the model lists more than you expect

`ubuntu-image` does not resolve content providers implicitly — every snap that
satisfies a content interface has to be named in the model, or it refuses with
*"prerequisites need to be added explicitly"*. So `image/model.json` carries
`mesa-2404` (`gpu-2404`, for Frame), plus `gnome-46-2404`, `gtk-common-themes` and
`bare`, which the client pulls in through the gnome extension.

If you add a snap to the image later and the build fails this way, the missing
provider is named in the error — add it to the model rather than to the `--snap`
arguments.

## What the job builds

1. Pads `assets/visiontak-logo.png` to 800×400 on black — the splash wants 2:1, and
   stretching a hexagon is obvious on a wall display.
2. Clones `canonical/pi-gadget` branch `24` and drops the logo at
   `splash/vendor-logo.png`. The Pi gadget already enables the splash (`splash` and
   `vt.handoff=2` are in `configs/cmdline.txt`), so supplying the file is the whole
   change.
3. Adds the `hdmi-cec` `custom-device` slot to the gadget's **`snapcraft.yaml`** — slots
   live there, not in `gadget.yaml`, which accepts a `slots:` block and ignores it —
   and the matching `connections:` entry to `gadget.yaml`, which is where that does
   belong. CEC is then an auditable property of the image rather than a reason to drop
   confinement.
4. Signs a system-user assertion for SSH, builds the gadget, signs the model, and runs
   `ubuntu-image`.

Output lands as the **`visiontak-pi-sdcard-image`** artifact, containing
`visiontak_client.img.xz`.

## Burning it

Raspberry Pi Imager (*Use custom*) or:

```sh
xzcat visiontak_client.img.xz | sudo dd of=/dev/sdX bs=4M status=progress conv=fsync
```

Check `lsblk` first — `dd` to the wrong device takes your disk with it.

Use a card of **8 GB or more**. The partition table is laid out at build time and does
not shrink to fit: 2500M of `ubuntu-seed`, 750M of `ubuntu-boot`, 32M of `ubuntu-save`
and 2500M of `ubuntu-data` come to roughly 5.6 GB expanded. The download stays near
1 GB because the free space compresses away.

`ubuntu-data` is sized generously on purpose. It holds every snap installed at first
boot — kernel, core24, mesa, `gnome-46-2404`, `gtk-common-themes`, `ubuntu-frame` and
the client — and the gadget's stock 1500M left almost no margin. Overrun it and the
device sits on "Installing Ubuntu Core" indefinitely with nothing on screen to say why.
Both sizes are overridable: `SEED_SIZE` and `DATA_SIZE`.

## First boot

`console-conf` is disabled through gadget `defaults`, so the device boots straight to
the kiosk and asks for the server address on screen. DHCP discovery is off by default —
see [`dhcp-discovery.md`](dhcp-discovery.md) for why.

The image also carries a signed system-user assertion, so there is a way in when
something needs looking at:

```sh
ssh -i /var/lib/visiontak-vm/ssh_key visiontak@<device>
```

Key-only — no password exists. Swap `image/authorized-keys.pub` for your own key to
change who can get in.

## Building it locally

Same script, on an arm64 host:

```sh
BRAND_ID=<account-id> MODEL_KEY=visiontak \
  image/build-image.sh dist/visiontak-client_0.1.0_arm64.snap
```

It refuses to run on x86 — the gadget cannot be cross-built for the same reason the
client cannot; see [`arm64-build.md`](arm64-build.md).

## Interfaces that need connecting

Publishing a slot in the gadget does not connect it. snapd auto-connects only where a
built-in rule or a store snap-declaration allows it, and `custom-device` has neither —
so the image lists the connection in `gadget.yaml` for snapd to make at first boot.

If CEC still does nothing, check it on the device:

```sh
snap connections visiontak-client | grep hdmi-cec   # slot should be pi:hdmi-cec
ls -l /dev/cec*
```

An unconnected plug is fixable in place, now that the image has SSH:

```sh
sudo snap connect visiontak-client:hdmi-cec pi:hdmi-cec
sudo snap restart visiontak-client
```

Press `i` on a keyboard afterwards: the panel should read `KernelCecBackend` rather
than `NullCecBackend (/dev/cec0 missing…)`.

The same applies to `network-setup-observe`, which DHCP discovery needs in order to
read the lease:

```sh
snap connections visiontak-client | grep network-setup-observe
sudo snap connect visiontak-client:network-setup-observe
```
