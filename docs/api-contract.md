# VisionTAK Server contract

**Confirmed** against a live instance on 2026-08-07 (`http://localhost:3001`).
Re-check any time with:

```bash
python -m visiontak_client.probe http://localhost:3001 --token "$TOKEN"
```

## The one endpoint the kiosk needs

### `GET /api/v1/client/config`

```http
Authorization: Bearer <api-token>
```

```json
{
  "defaultDashboardId": null,
  "allowedDashboards": [
    { "id": "26f96ec8-e09c-4e1f-9b57-1e9da2d87a98", "name": "Shack TV" }
  ]
}
```

- **Token is enforced.** A missing or wrong token returns `401`. The client raises
  `AuthError` for 401/403 and backs off to 15 minutes rather than retrying every
  refresh interval — a rejected token will not fix itself.
- `defaultDashboardId` may be `null`. When set, it selects the boot dashboard. The
  snap's `start-dashboard` setting overrides it.
- Entries carry **no URL**.

## Deriving the dashboard URL

Dashboards are rendered by the server's own Next.js app at:

```
{server-url}/view/{dashboardId}
```

`GET /view/{id}` returns `Live Dashboards | VisionTAK`, server-rendered, with the
settings and `clientHints` (`refreshIntervalMs: 60000`, `cacheTtlSeconds: 30`) inlined
in the RSC payload. **It is not behind the sign-in gate**, which is what makes the
kiosk workable — a display with no keyboard cannot complete an interactive login.

Two consequences to be aware of:

- Every other route (`/`, `/display/{id}`, `/kiosk/{id}`, …) redirects to
  `Sign in | VisionTAK`. `/view/{id}` is the only usable one.
- `/view/{id}` does not validate the id server-side — any string returns the shell and
  resolution happens client-side. So a stale id in the cache produces an empty view,
  not an error. The client only ever uses ids the server just handed it.

## Catch-all caveat

The server is a Next.js app with a catch-all route: **an unknown path returns HTTP 200
with the SPA shell**, not a 404. Status code alone proves nothing. `api.py` checks the
response `Content-Type` for JSON and raises a specific error naming this behaviour if
it gets HTML, so a future path change surfaces as a clear log line instead of a blank
screen.

## Admin endpoints (not used by the kiosk)

```
GET /api/v1/dashboards            -> {"dashboards":[{id,name,description,isEnabled,layoutId,sortOrder}]}
GET /api/v1/dashboards/{id}       -> {id,name,description,isEnabled,layout:{id,name,version},refreshSeconds,themeOverride,sortOrder}
GET /api/v1/layouts               -> {"layouts":[{id,name,description,version,isActive}]}
GET /api/v1/layouts/{id}          -> {…,"definition":{"grid":{rows,columns,gap},"regions":[{id,name,x,y,w,h,zIndex}]}}
```

The parsers handle `sortOrder` and `isEnabled` from this shape too, so pointing the
client at these endpoints later would be a one-line change.

> ⚠️ **Both answer with no token at all.** Anything that can reach port 3001 can
> enumerate every dashboard and layout in the system. That is a server-side issue, not
> a client one, and the kiosk does not depend on those endpoints — but it undercuts the
> per-device token model, so it is worth fixing on the server.

## Endpoints that do not exist

Confirmed absent (`404`, as opposed to the SPA catch-all): `/api/v1/client/dashboards`,
`/api/v1/client/register`, `/api/v1/client/state`, `/api/v1/client/heartbeat`,
`/api/v1/devices`, `/api/v1/health`, `/api/v1/version`,
`/api/v1/dashboards/{id}/{panels,widgets,regions,…}`.

There is therefore **no device enrolment and no heartbeat**. The client does not report
its state anywhere; the server cannot currently tell whether a display is alive or what
it is showing. If that matters, it needs adding server-side first.

## Not yet answered

- [ ] How is a client token issued and revoked? Is it per-device or shared?
- [ ] Does the token ever expire, and is there a refresh path?
- [ ] Is there a push channel (SSE/websocket)? The client polls
      `/api/v1/client/config` every `refresh-interval` seconds; push would be better.
- [ ] Can the server pin a dashboard to a specific device, beyond the global
      `defaultDashboardId`?
- [ ] Does `/view/{id}` self-refresh its data, or does the kiosk need to reload it?
      `clientHints.refreshIntervalMs: 60000` suggests the page refreshes itself.
