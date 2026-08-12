# Zero-touch enrolment with DHCP option 225

A display that finds its own server needs no console: plug it into the right VLAN and
it enrols itself. The on-screen setup prompt remains the fallback for networks that do
not serve the option.

## What the client does on first boot

Only when `server-url` is unset:

1. Read DHCP option 225 from the lease.
2. Save it as `server-url`.
3. `POST /api/v1/client/register` with `{"deviceId": …, "name": …}`.
4. Save any token the server issues.

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

`POST /api/v1/client/register`, sending `deviceId` and `name`. A token is taken from
whichever of `token`, `apiToken`, `api_token`, `accessToken` or `access_token` the
response carries, because the contract is not documented server-side yet and a first
boot on a headless device is a bad place to discover a naming difference.

A server that enrols without issuing a token is treated as success — some deployments
authorise by device-id alone.
