"""First-boot enrolment: find the server, register, persist what we learned.

Registration is not one-shot. A new device comes back "pending" until an admin
approves it in the server UI, so the device has to keep asking — possibly for days,
across reboots — and pick the token up the moment it is approved.

Everything here is best-effort. A failure at any step leaves the display running and
falls through to the on-screen prompt, because a unit that cannot self-enrol should
still be recoverable by someone standing in front of it.
"""

from __future__ import annotations

import dataclasses
import logging

from .api import ApiError, Registration, VisionTakClient
from .config import Config, persist
from .discovery import discover_server_url

log = logging.getLogger(__name__)

# An admin approving a device is a human action, so poll gently. Fast enough that
# nobody watches a blank screen after clicking approve; slow enough to be invisible
# in the server's logs.
POLL_SECONDS = 20


def discover(config: Config) -> Config:
    """Fill in server_url from DHCP option 225 if it is not already set."""
    if config.server_url:
        return config
    url = discover_server_url()
    if not url:
        return config
    try:
        persist("server-url", url)
    except Exception as exc:  # noqa: BLE001 - never fatal on a headless device
        log.warning("discovered %s but could not persist it: %s", url, exc)
    return dataclasses.replace(config, server_url=url)


def attempt_registration(config: Config) -> tuple[Registration | None, Config]:
    """Ask the server to enrol this device.

    Returns the server's answer (or None if it could not be reached) and the config to
    carry on with, which gains the token if one was issued.
    """
    if not config.server_url or config.api_token:
        return None, config

    try:
        result = VisionTakClient(config).register(label=config.osd_name)
    except ApiError as exc:
        # An older server without the endpoint, or one that is simply down. Neither is
        # worth stopping for: /view/{id} may still render without a token.
        log.warning("registration at %s failed: %s", config.server_url, exc)
        return None, config

    if result.pending:
        log.info("registered; waiting for an admin to approve this device")
        return result, config

    if result.approved and result.token:
        try:
            persist("api-token", result.token)
        except Exception as exc:  # noqa: BLE001
            # Keep it in memory regardless — losing it means it can never be re-issued.
            log.error("approved but could not persist the token: %s", exc)
        return result, dataclasses.replace(config, api_token=result.token)

    return result, config
