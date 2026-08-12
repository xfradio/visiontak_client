"""Find the VisionTAK server from DHCP option 225.

A display that discovers its own server needs no console at all: plug it into the
right VLAN and it finds home. The on-screen setup prompt stays as the fallback for
networks that do not serve the option.

Option 225 is in the site-local range, so its meaning is ours to define. Accepted
forms, in the order people actually write them:

    10.0.0.5:3000
    10.0.0.5
    http://10.0.0.5:3000
    https://visiontak.example

Reading it means reading systemd-networkd's lease, because that is where a received
option ends up on Ubuntu Core. Note that networkd only records options it asked for:
the interface must carry `RequestOptions=225`, or the server's answer is discarded
before it reaches this code. See docs/dhcp-discovery.md.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .config import normalise_server_url

log = logging.getLogger(__name__)

OPTION = 225

# systemd-networkd writes leases here, one file per interface index.
LEASE_DIRS = ("/run/systemd/netif/leases",)

# Different systemd versions spell a private option differently in the lease file.
_KEY_PATTERNS = (
    re.compile(rf"^OPTION_{OPTION}=(.*)$"),
    re.compile(rf"^PRIVATE_{OPTION}=(.*)$"),
    re.compile(rf"^VENDOR_OPTION_{OPTION}=(.*)$"),
)


def _decode(value: str) -> str:
    """Leases may carry the option as text or as hex bytes."""
    text = value.strip().strip('"')
    if not text:
        return ""
    # A hex payload is how systemd renders an option it has no type for.
    compact = text.replace(":", "").replace(" ", "")
    if len(compact) >= 4 and len(compact) % 2 == 0 and re.fullmatch(r"[0-9a-fA-F]+", compact):
        try:
            decoded = bytes.fromhex(compact).decode("utf-8", "strict").strip("\x00").strip()
        except (ValueError, UnicodeDecodeError):
            return text
        # Only prefer the decoded form if it looks like an address rather than noise.
        if decoded and all(c.isprintable() for c in decoded):
            return decoded
        return text
    return text


def parse_option(value: str) -> str:
    """Turn a raw option 225 payload into a server URL, or '' if unusable."""
    text = _decode(value)
    if not text:
        return ""
    return normalise_server_url(text)


def _lease_files(dirs: tuple[str, ...]) -> list[Path]:
    found: list[Path] = []
    for directory in dirs:
        path = Path(directory)
        if not path.is_dir():
            continue
        try:
            found.extend(sorted(p for p in path.iterdir() if p.is_file()))
        except OSError as exc:
            log.debug("cannot list %s: %s", path, exc)
    return found


@dataclass(frozen=True)
class Discovery:
    """What discovery found, and something a human can read on the screen.

    The detail matters more than it looks: a field unit has no login, so if discovery
    comes up empty the only way to tell "no lease readable" from "lease read, no
    option" from "option present but malformed" is for the device to say so itself.
    """

    url: str = ""
    detail: str = ""


def describe(dirs: tuple[str, ...] = LEASE_DIRS) -> Discovery:
    leases = _lease_files(dirs)
    if not leases:
        return Discovery(detail=f"DHCP option {OPTION}: no lease found")

    unreadable = 0
    for lease in leases:
        try:
            content = lease.read_text(errors="replace")
        except OSError:
            unreadable += 1
            continue
        for line in content.splitlines():
            for pattern in _KEY_PATTERNS:
                match = pattern.match(line.strip())
                if not match:
                    continue
                url = parse_option(match.group(1))
                if url:
                    return Discovery(url=url, detail=f"DHCP option {OPTION}: {url}")
                return Discovery(
                    detail=f"DHCP option {OPTION}: unusable value {match.group(1)!r}"
                )

    if unreadable:
        # Almost always the snap interface, not the network.
        return Discovery(
            detail=(
                f"DHCP option {OPTION}: lease unreadable — is network-setup-observe "
                "connected?"
            )
        )
    return Discovery(detail=f"DHCP option {OPTION}: not offered on this network")


def discover_server_url(dirs: tuple[str, ...] = LEASE_DIRS) -> str:
    """The server URL advertised by DHCP, or '' when the network does not say.

    Never raises: discovery failing is an ordinary state that falls through to the
    setup screen, not an error worth stopping a display from starting for.
    """
    result = describe(dirs)
    log.info("%s", result.detail)
    return result.url
