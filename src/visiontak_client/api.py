"""VisionTAK Server REST client.

Stdlib only, on purpose: every third-party package is more code inside a strictly
confined snap that ships to unattended displays.

Contract confirmed against a live server — see docs/api-contract.md. The client needs
exactly one endpoint:

    GET /api/v1/client/config
    Authorization: Bearer <token>
    -> {"defaultDashboardId": null, "allowedDashboards": [{"id": …, "name": …}]}

Dashboards have no URL of their own; each is rendered at `{server}/view/{id}`.
"""

from __future__ import annotations

import json
import logging
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config
from .models import Dashboard, sort_dashboards

log = logging.getLogger(__name__)

CLIENT_CONFIG_PATH = "/api/v1/client/config"
REGISTER_PATH = "/api/v1/client/register"
DEVICE_TYPE = "raspberry_pi"
LABEL_MAX_LEN = 120


@dataclass(frozen=True)
class Registration:
    """The server's answer to an enrolment attempt.

    Three outcomes matter and they are not interchangeable:
      pending          — an admin has not approved this device yet; ask again later.
      approved + token — the one and only delivery of that token. Persist it now.
      approved, no token — already approved and the token was handed out before. If
                           we do not have it, nobody can re-issue it from here.
    """

    status: str
    token: str = ""

    @property
    def pending(self) -> bool:
        return self.status == "pending"

    @property
    def approved(self) -> bool:
        return self.status == "approved"

# The server is a Next.js app with a catch-all route: an unknown path returns the SPA
# shell with HTTP 200 rather than a 404. A JSON parse failure therefore means "wrong
# path", not "server broken", and is reported as such.
_COLLECTION_KEYS = ("allowedDashboards", "dashboards", "data", "items", "results")


class ApiError(RuntimeError):
    """Any failure talking to VisionTAK Server."""


class AuthError(ApiError):
    """The token was rejected. Retrying will not help until it is replaced."""


@dataclass(frozen=True)
class ClientConfig:
    """What the server says this client may show."""

    dashboards: list[Dashboard] = field(default_factory=list)
    default_dashboard_id: str | None = None


def unwrap_collection(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in _COLLECTION_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise ApiError(f"no dashboard collection in response of type {type(payload).__name__}")


def parse_client_config(payload: Any, base_url: str) -> ClientConfig:
    dashboards = []
    for entry in unwrap_collection(payload):
        if not isinstance(entry, dict):
            log.warning("skipping non-object dashboard entry: %r", entry)
            continue
        if not Dashboard.is_enabled(entry):
            log.info("skipping disabled dashboard %r", entry.get("name") or entry.get("id"))
            continue
        try:
            dashboards.append(Dashboard.from_payload(entry, base_url))
        except ValueError as exc:
            log.warning("skipping unusable dashboard entry: %s", exc)

    default_id = payload.get("defaultDashboardId") if isinstance(payload, dict) else None
    return ClientConfig(
        dashboards=sort_dashboards(dashboards),
        default_dashboard_id=str(default_id) if default_id else None,
    )


class VisionTakClient:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._ssl_context = None if config.verify_tls else ssl._create_unverified_context()
        if not config.verify_tls:
            log.warning("TLS verification is DISABLED — lab use only")

    @property
    def base_url(self) -> str:
        return self._config.server_url.rstrip("/")

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        if not self.base_url:
            raise ApiError("server-url is not configured")
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", f"visiontak-client ({self._config.device_id})")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self._config.api_token:
            request.add_header("Authorization", f"Bearer {self._config.api_token}")
        try:
            with urllib.request.urlopen(
                request, timeout=self._config.request_timeout, context=self._ssl_context
            ) as response:
                raw = response.read()
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise AuthError(f"{method} {path} -> HTTP {exc.code}: api-token rejected") from exc
            raise ApiError(f"{method} {path} -> HTTP {exc.code} {exc.reason}") from exc
        except (urllib.error.URLError, OSError, ssl.SSLError) as exc:
            raise ApiError(f"{method} {path} -> {exc}") from exc

        if "json" not in content_type.lower():
            raise ApiError(
                f"{method} {path} returned {content_type or 'no content-type'}, not JSON "
                "— the path is probably wrong (this server serves an SPA shell on 200)"
            )
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError(f"{method} {path} returned non-JSON ({len(raw)} bytes)") from exc

    def fetch_client_config(self) -> ClientConfig:
        return parse_client_config(self._request("GET", CLIENT_CONFIG_PATH), self.base_url)

    def register(self, *, label: str = "") -> Registration:
        """Enrol this device. The only endpoint callable before we hold a token.

        Safe to call repeatedly: the server recognises a repeat by deviceId, which is
        why that id must be stable across reboots.
        """
        payload: dict[str, Any] = {
            "deviceId": self._config.device_id,
            "deviceType": DEVICE_TYPE,
        }
        if label:
            payload["label"] = label[:LABEL_MAX_LEN]

        body = self._request("POST", REGISTER_PATH, payload)
        if not isinstance(body, dict):
            raise ApiError(f"register returned {type(body).__name__}, not an object")

        status = str(body.get("status") or "")
        raw_token = body.get("token")
        token = raw_token if isinstance(raw_token, str) and raw_token else ""

        if status == "approved" and not token:
            # Not an error the device can fix: the token was delivered on an earlier
            # call and the server will not repeat it. Say so plainly, because the
            # symptom otherwise is an approved device that still cannot authenticate.
            log.warning(
                "device is approved but the server did not return a token — it was "
                "delivered previously. If this device does not have it, an admin must "
                "re-issue the registration."
            )
        return Registration(status=status, token=token)


class ConfigCache:
    """Last known-good client config, so a server outage does not blank the screen."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._last_written: str | None = None

    def save(self, client_config: ClientConfig) -> None:
        payload = {
            "defaultDashboardId": client_config.default_dashboard_id,
            "allowedDashboards": [
                {"id": d.id, "name": d.name, "sortOrder": d.order} for d in client_config.dashboards
            ],
        }
        blob = json.dumps(payload, indent=2)
        # The refresh loop calls this on every poll, whether or not the server said
        # anything new — several hundred rewrites a day of a file that changes when an
        # admin edits a dashboard. That is write cycles spent on an SD card, which is
        # the part of a Pi most likely to fail and the one that takes the unit with it.
        if blob == self._last_written and self._path.is_file():
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(blob)
        tmp.replace(self._path)
        self._last_written = blob

    def load(self, base_url: str) -> ClientConfig:
        if not self._path.is_file():
            return ClientConfig()
        try:
            return parse_client_config(json.loads(self._path.read_text()), base_url)
        except (json.JSONDecodeError, ApiError, OSError) as exc:
            log.warning("config cache unreadable, ignoring: %s", exc)
            return ClientConfig()
