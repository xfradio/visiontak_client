"""Where this device is on the network.

Exists for the diagnostics overlay: a wall-mounted display gives no other clue what
address to SSH to, and Ubuntu Core leaves every unit called `localhost`, so the
hostname is no help either.
"""

from __future__ import annotations

import logging
import socket
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# Any address on a route will do — connect() on a UDP socket only selects an
# interface, it sends nothing. This one is reserved and never routed off-link.
_FALLBACK_TARGET = "10.255.255.255"


def local_ip(server_url: str = "") -> str:
    """This device's address on the interface facing the server, or ''.

    A device with wifi and ethernet has more than one answer, and the one worth
    printing is the one that reaches the server — that is the address support will
    connect back on.
    """
    target = ""
    if server_url:
        target = urlparse(server_url).hostname or ""
    target = target or _FALLBACK_TARGET

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.5)
            # Port 9 is discard. Nothing is transmitted; this only fixes the route.
            sock.connect((target, 9))
            return str(sock.getsockname()[0])
    except OSError as exc:
        # No route, no DNS, no link. Not worth failing an overlay over.
        log.debug("could not determine local address via %s: %s", target, exc)
        return ""
