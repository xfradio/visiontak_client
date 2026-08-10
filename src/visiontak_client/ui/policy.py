"""Navigation policy for the kiosk webviews.

A kiosk that will happily follow any link is a kiosk that can be walked off its own
site. Navigation is restricted to hosts we were configured with; everything else is
refused and logged. This complements, not replaces, the snap confinement.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

log = logging.getLogger(__name__)

ALWAYS_ALLOWED_SCHEMES = {"about", "data", "blob"}

# `allowed-hosts=*` turns the allowlist off. Dashboards that embed third-party content
# — maps, Grafana on another host, video — cannot render otherwise, and there is no
# way to enumerate in advance what a dashboard author will embed. It is deliberately a
# single explicit value rather than the default, so `snap get` shows plainly that a
# device is running open, and an image that does not set it stays restricted.
WILDCARD = "*"


class HostAllowlist:
    def __init__(self, urls: list[str] | None = None) -> None:
        self._hosts: set[str] = set()
        self._allow_any = False
        for url in urls or []:
            self.allow(url)

    def allow(self, url: str) -> None:
        if url.strip() == WILDCARD:
            self._allow_any = True
            return
        # Bare hostnames ("pskreporter.info") have no scheme, and urlparse puts those
        # in .path rather than .hostname — so accept both spellings.
        parsed = urlparse(url if "//" in url else f"//{url}")
        host = parsed.hostname
        if host:
            self._hosts.add(host.lower())

    @property
    def allows_any(self) -> bool:
        return self._allow_any

    @property
    def hosts(self) -> set[str]:
        return set(self._hosts)

    def permits(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme in ALWAYS_ALLOWED_SCHEMES:
            return True
        if self._allow_any:
            return True
        host = (parsed.hostname or "").lower()
        if not host:
            return False
        # Allow exact hosts and their subdomains, so a dashboard on
        # grafana.visiontak.example still works when the server is visiontak.example.
        return any(host == allowed or host.endswith("." + allowed) for allowed in self._hosts)


def make_policy_handler(allowlist: HostAllowlist):
    """Build a `decide-policy` handler for a WebKit.WebView."""
    import gi

    gi.require_version("WebKit", "6.0")
    from gi.repository import WebKit

    def on_decide_policy(_view, decision, decision_type) -> bool:
        if decision_type not in (
            WebKit.PolicyDecisionType.NAVIGATION_ACTION,
            WebKit.PolicyDecisionType.NEW_WINDOW_ACTION,
        ):
            return False
        url = decision.get_navigation_action().get_request().get_uri()
        if decision_type == WebKit.PolicyDecisionType.NEW_WINDOW_ACTION:
            # Never spawn a second surface: keep pop-ups in the current view if the
            # target is permitted, otherwise drop them.
            decision.ignore()
            return True
        if allowlist.permits(url):
            return False
        log.warning("blocked navigation to %s (allowed hosts: %s)", url, sorted(allowlist.hosts))
        decision.ignore()
        return True

    return on_decide_policy
