# Zero-touch enrolment with DHCP option 225

A display that finds its own server needs no console: plug it into the right VLAN and
it enrols itself. The on-screen setup prompt remains the fallback for networks that do
not serve the option.

## What the client does on first boot

Only when `server-url` is unset:

1. Read DHCP option 225 from the lease.
2. Save it as `server-url`.
3. `POST /api/v1/client/register` with `{"deviceId": …, "deviceType": "raspberry_pi",
   "label": …}`, and keep polling while the answer is `pending`.
4. Save the token the moment approval delivers one.

Every step is best-effort. Discovery finding nothing, a malformed option, an
unreachable server or a registration error all fall through to the setup screen — a
display that cannot self-enrol must still be fixable by someone standing in front of
it.

The address is saved *before* registration. If registration fails the address is still
correct, and the next boot retries rather than asking a human to retype what DHCP
already said.

## Serving the option

Option 225 is in the site-local range, so its meaning is yours to define. The client
accepts, in the order people actually write them:

```
10.0.0.5:3000
10.0.0.5
http://10.0.0.5:3000
https://visiontak.example
```

Anything else is rejected and the setup screen appears. That rejection matters: a
malformed option saved as the server address would leave a unit with no login pointing
at nonsense forever.

**ISC dhcpd**

```
option visiontak-server code 225 = text;
subnet 10.0.0.0 netmask 255.255.255.0 {
  option visiontak-server "10.0.0.5:3000";
}
```

**dnsmasq**

```
dhcp-option=225,"10.0.0.5:3000"
```

## The client must ask for it

This is the part that catches people out. **systemd-networkd only records options it
requested.** A server that helpfully sends 225 unsolicited has it discarded before the
client ever sees it, and discovery will report nothing found with no other symptom.

The interface needs `RequestOptions=225`, in a networkd drop-in:

```ini
[DHCPv4]
RequestOptions=225
```

On Ubuntu Core the writable path for this is `/etc/systemd/network/`, so on a locked
down image this has to be shipped rather than typed. **This has not been verified on a
device** — if discovery reports nothing while the DHCP server is definitely serving
225, this is the first thing to check.

Confirm what the lease actually holds:

```sh
sudo grep -r . /run/systemd/netif/leases/
```

## Confinement

Reading those leases needs the `network-setup-observe` interface, which the snap plugs.
It is observe-only and grants no ability to change the network. If it is unconnected,
discovery logs the file it could not read and falls through to setup.

```sh
snap connections visiontak-client | grep network-setup-observe
```

## Registration

`POST /api/v1/client/register` (unauthenticated — the one endpoint a device without a
token may call), sending `deviceId`, `deviceType: raspberry_pi` and `label`.

Once a token is held, it goes on every other endpoint as
`Authorization: Bearer <token>`.

### Approval is a human step

The server answers one of three ways:

| Response | Meaning |
|---|---|
| `{"status":"pending"}` | An admin has not approved this device yet |
| `{"status":"approved","token":"…"}` | The one and only delivery of that token |
| `{"status":"approved","token":null}` | Approved, but the token was handed out already |

So enrolment is a **poll, not a call**. The client re-registers every 20s while
pending and picks the token up on its own the moment an admin approves — no power
cycle timed to the approval. The screen says *"Waiting for approval on the server"*
meanwhile, rather than an unexplained "Waiting for dashboards…".

The third case is the one that bites: if this device does not already hold the token,
nobody can retrieve it — an admin has to re-issue the registration. The client logs
that explicitly instead of looping silently.

### deviceId

Client-generated, persisted, and reused on every call — that reuse is what lets the
server recognise the same device asking again. The client stores `visiontak_client_<uuid4>` via
`snap set device-id`.

It deliberately does **not** use the hostname: Ubuntu Core leaves every unit as
`localhost`, so a whole fleet would have enrolled as one device.

## How the image ships the request

`image/cloud-init.yaml` is staged into the gadget snap as **`cloud.conf`**, which is
how Ubuntu Core 20 and later take cloud-init configuration. It writes the drop-in and
restarts networkd on first boot.

`ubuntu-image --cloud-init` does *not* work here despite the flag existing — UC20+
models reject it outright:

```
Error preparing image: cannot support with UC20+ model requested customizations:
cloud-init user-data
```

Note that `cloud.conf` is staged through the same injected part as the splash, because
the gadget's single part sources only `configs/` and snapcraft auto-includes nothing
but `gadget.yaml`. The build verifies both landed inside the built snap rather than
trusting that copying them into the tree was enough.

Canonical describe gadget cloud-init as development-oriented rather than production.
For a fleet, fold the same `.network` file into a properly maintained gadget fork
instead of relying on cloud-init to write it at first boot.

It writes `/etc/systemd/network/05-visiontak-dhcp.network`. `05-` sorts ahead of
netplan's generated `10-netplan-*.network` and systemd applies the first match, so it
takes over DHCP for wired interfaces — the same DHCP netplan was doing, plus the
option request.

**Wired only.** A wifi kiosk still needs netplan for the supplicant and would need
this expressed as a drop-in against the generated file instead.

## Telling the failure modes apart without a login

A field unit has no console, so the setup screen prints what discovery saw:

| On screen | Means |
|---|---|
| `not offered on this network` | Lease read fine; the server sent no 225, or networkd was not asking |
| `lease unreadable — is network-setup-observe connected?` | The snap interface, not the network |
| `no lease found` | networkd has not leased anything yet |
| `unusable value '…'` | The option arrived but is not an address |

Without that line the three are indistinguishable from "it didn't work".

## Status: off by default

`dhcp-discovery` defaults to `false`. The client goes straight to the setup screen and
asks for the address. To try discovery again:

```sh
sudo snap set visiontak-client dhcp-discovery=true
sudo snap connect visiontak-client:network-setup-observe
```

### Why it is off

Nothing has been found that makes systemd-networkd request option 225 on an Ubuntu
Core image, and without the request the server's answer is discarded before the client
can read it.

| Attempt | Outcome |
|---|---|
| netplan `dhcp4-overrides` | `RequestOptions` is a systemd-networkd setting; netplan cannot express it |
| `ubuntu-image --cloud-init` | Rejected: *"cannot support with UC20+ model requested customizations"* |
| gadget `cloud.conf` | Reaches the device — snapd installs it as `/etc/cloud/cloud.cfg.d/80_device_gadget.cfg` — but never runs |

The last one is the interesting failure. Verified on hardware:

```
$ cloud-init status --long
status: disabled
boot_status_code: disabled-by-marker-file
detail: Cloud-init disabled by /etc/cloud/cloud-init.disabled
```

Ubuntu Core writes that marker when a device seeds without a datasource. Gadget
`cloud.conf` supplies *configuration*, not a *datasource*, so cloud-init disables
itself before executing a single module — which is why `write_files` never ran and
`journalctl -u cloud-init` is empty. This is what Canonical mean by describing gadget
cloud-init as development-only.

The client-side code is complete and tested; only delivery of the networkd drop-in is
unsolved. Options if this is picked up again:

1. Give cloud-init a NoCloud datasource so Core stops disabling it.
2. Write the drop-in over SSH per device — fine for a handful, not for a fleet.
3. Query DHCP from inside the client (DHCPINFORM for option 225), which depends on
   nothing outside the snap but has to coexist with networkd on port 68.

To confirm the rest of the chain independently of delivery, write the file by hand:

```sh
sudo tee /etc/systemd/network/05-visiontak-dhcp.network >/dev/null <<'NET'
[Match]
Name=e*

[Network]
DHCP=ipv4

[DHCPv4]
RequestOptions=225
NET
sudo systemctl restart systemd-networkd
sudo grep -r . /run/systemd/netif/leases/
```
