"""First-boot enrolment: find the server, register, persist what we learned.

Runs only when no server address is configured. Everything here is best-effort — a
failure at any step falls through to the on-screen setup prompt, because a display
that cannot self-enrol should still be recoverable by someone standing in front of it.
"""

from __future__ import annotations

import logging

from .api import ApiError, VisionTakClient
from .config import Config, persist
from .discovery import discover_server_url

log = logging.getLogger(__name__)


def enrol(config: Config) -> Config:
    """Try to configure this device from the network. Returns the config to run with.

    Returns the original config unchanged when discovery or registration fails, which
    leaves server_url empty and so raises the setup screen.
    """
    if config.server_url:
        return config

    url = discover_server_url()
    if not url:
        return config

    # Persist the address before registering. If registration fails the address is
    # still right, and the next boot retries enrolment rather than asking a human to
    # retype something DHCP already told us.
    candidate = replace_server_url(config, url)
    try:
        persist("server-url", url)
    except Exception as exc:  # noqa: BLE001 - never fatal on a headless device
        log.warning("discovered %s but could not persist it: %s", url, exc)
        return candidate

    token = ""
    try:
        token = VisionTakClient(candidate).register(osd_name=config.osd_name)
    except ApiError as exc:
        # An unreachable or older server is not a reason to stop: the client config
        # endpoint may still authorise us, and the dashboards may need no token.
        log.warning("registration at %s failed: %s", url, exc)
        return candidate

    if token:
        try:
            persist("api-token", token)
        except Exception as exc:  # noqa: BLE001
            log.warning("registered but could not persist the token: %s", exc)
            return replace_api_token(candidate, token)
        return replace_api_token(candidate, token)
    return candidate


def replace_server_url(config: Config, url: str) -> Config:
    return _replace(config, server_url=url)


def replace_api_token(config: Config, token: str) -> Config:
    return _replace(config, api_token=token)


def _replace(config: Config, **changes) -> Config:
    from dataclasses import replace

    return replace(config, **changes)
