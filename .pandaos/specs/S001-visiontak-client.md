# S001 — VisionTAK Client (Ubuntu Core Kiosk)

**Status:** implemented (first pass)
**Target:** Raspberry Pi 4/5, Ubuntu Core 24, strict confinement

## Objective

A locked-down appliance that boots straight into a full-screen VisionTAK dashboard
and is driven entirely from the TV remote over HDMI-CEC. No keyboard, no mouse, no
desktop session, no shell exposed to the operator.

## Non-goals

- Local dashboard authoring. Dashboards are authored server-side; the client renders them.
- Multi-window / multi-monitor. One output, one full-screen surface.
- Offline authoring. Offline behaviour is limited to replaying the cached dashboard list.

## Stack decisions

| Layer | Choice | Why |
|---|---|---|
| OS | Ubuntu Core 24 | Transactional updates, strict confinement, no package manager on device |
| Display server | Ubuntu Frame (Mir, Wayland-only) | No X11 attack surface; Mir enforces per-client surface isolation; Canonical-supported kiosk shell |
| Client | GTK4 + WebKitGTK 6.0, single snap | One process owns webviews + CEC → instant dashboard switching, no browser restart |
| CEC | Kernel CEC uAPI (`/dev/cec0`) via ctypes | vc4 DRM driver exposes CEC natively on Pi; avoids shipping/patching libcec. libcec backend kept for USB adapters |
| Transport | stdlib `urllib` over HTTPS | Zero third-party deps inside the snap → smaller trusted computing base |

### Why Wayland/Mir is the security story

- Ubuntu Frame is Wayland-only. There is no X11 socket, so the classic "any client can
  keylog/screenshot every other client" X11 failure mode is structurally absent.
- Mir hands each client only its own surface; the kiosk cannot enumerate or capture others.
- The snap plugs `wayland` and talks to Frame over the confined Wayland socket. It has no
  `x11`, no `unity7`, no `desktop-legacy`.
- `/dev/cec0` is *not* reachable through any default interface. It is granted explicitly by a
  gadget-provided `custom-device` slot (see `docs/ubuntu-core-image.md`), so CEC access is an
  auditable property of the image, not an ambient capability.

## Functional requirements

1. **FR-1** Boot to a full-screen dashboard with no operator interaction.
2. **FR-2** Fetch the allowed dashboards from `GET /api/v1/client/config`; render each at
   `{server}/view/{id}`; cache the result and fall back to it when the server is unreachable.
3. **FR-3** CEC remote: open/close the dashboard chooser, navigate it, pick a dashboard,
   step to next/previous dashboard, jump by number key, reload, blank/standby.
4. **FR-4** Announce as a CEC playback device with OSD name, and request the TV switch input
   to us at start-up (`IMAGE_VIEW_ON` + `ACTIVE_SOURCE`).
5. **FR-5** Survive TV power cycles and HDMI hot-plug: re-claim the CEC logical address when
   the physical address changes.
6. **FR-6** Configurable entirely via `snap set` — no file editing on device.
7. **FR-7** Optional carousel: auto-rotate dashboards on an interval.
8. **FR-8** Crash → systemd restart → back on the last dashboard.

## Configuration surface (`snap set visiontak-client …`)

| Key | Default | Meaning |
|---|---|---|
| `server-url` | — (required) | VisionTAK Server base URL |
| `api-token` | — | Bearer token |
| `device-id` | hostname | Identity reported to the server |
| `cec-backend` | `auto` | `auto` \| `kernel` \| `libcec` \| `none` |
| `cec-device` | `/dev/cec0` | Adapter path |
| `osd-name` | `VisionTAK` | Name shown in the TV's source list (≤14 chars) |
| `refresh-interval` | `300` | Client-config poll, seconds |
| `rotate-interval` | `0` | Carousel period, seconds; `0` disables |
| `start-dashboard` | — | Dashboard id to show at boot; overrides the server's `defaultDashboardId` |
| `verify-tls` | `true` | Set false only for a lab CA |

## Acceptance

- `snap install` + `snap set server-url=…` on a fresh Ubuntu Core Pi yields a dashboard on
  screen within 20 s of power-on with no input device attached.
- TV remote arrow/OK/back drives the chooser end-to-end.
- Pulling the network shows the cached dashboard list; restoring it resyncs within
  `refresh-interval`.
- `snap connections visiontak-client` shows no `x11`, no `desktop-legacy`, no `home`.

## Open items

- **Token lifecycle.** A static bearer token in snap config. Unknown whether tokens are
  per-device, whether they expire, and how they are revoked. Needs a server-side answer.
- **No device enrolment or heartbeat exists server-side.** The server cannot tell which
  displays are alive or what they are showing. Would need `POST /api/v1/client/state`
  or similar adding before the client can report in.
- **Polling, not push.** The client polls `/api/v1/client/config`. An SSE/websocket
  channel would cut both latency and load.
- **`/api/v1/dashboards` and `/api/v1/layouts` are unauthenticated** (confirmed
  2026-08-07). Not used by the kiosk, but it undercuts the per-device token model.
- **Snap has never been built or run on hardware.** See the verification status in the
  README.
