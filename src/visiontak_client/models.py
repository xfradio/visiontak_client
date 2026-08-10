"""Domain types shared across the client.

Confirmed against a live VisionTAK Server (see docs/api-contract.md). Dashboards carry
no URL of their own: the server renders each one at `{server}/view/{id}`, so the client
derives the URL from the id.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

VIEW_PATH = "/view"

# The live server returns `id`/`name` on the client-config endpoint and adds
# `sortOrder`/`isEnabled` on the admin endpoint. A short alias list absorbs the
# difference and the odd camel/snake inconsistency.
_ALIASES: Mapping[str, Sequence[str]] = {
    "id": ("id", "dashboardId", "dashboard_id", "uuid"),
    "name": ("name", "title", "label"),
    "order": ("sortOrder", "sort_order", "order", "position"),
    "enabled": ("isEnabled", "is_enabled", "enabled"),
    "group": ("group", "groupName", "group_name", "category", "folder"),
}


def _pick(payload: Mapping[str, Any], field: str) -> Any:
    for alias in _ALIASES[field]:
        if alias in payload and payload[alias] is not None:
            return payload[alias]
    return None


def view_url(base_url: str, dashboard_id: str) -> str:
    """The server-rendered page for a dashboard."""
    return f"{base_url.rstrip('/')}{VIEW_PATH}/{dashboard_id}"


@dataclass(frozen=True)
class Dashboard:
    id: str
    name: str
    url: str
    order: int = 0
    # The chooser renders this as a subtitle when present. `/api/v1/client/config`
    # sends only {id, name}, so it is normally empty — absent means "no grouping",
    # not an error.
    group: str = ""

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], base_url: str) -> Dashboard:
        raw_id = _pick(payload, "id")
        if not raw_id:
            raise ValueError(f"dashboard payload has no id: {sorted(payload)}")
        name = _pick(payload, "name")
        return cls(
            id=str(raw_id),
            name=str(name) if name else str(raw_id),
            url=view_url(base_url, str(raw_id)),
            order=_as_order(_pick(payload, "order")),
            group=str(_pick(payload, "group") or ""),
        )

    @staticmethod
    def is_enabled(payload: Mapping[str, Any]) -> bool:
        """Absent means enabled — the client-config endpoint omits the flag entirely."""
        value = _pick(payload, "enabled")
        return True if value is None else bool(value)


def _as_order(value: Any) -> int:
    """Ordering is cosmetic, so a nonsense value sorts first rather than failing."""
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    return int(text) if text.lstrip("-").isdigit() else 0


def sort_dashboards(dashboards: Sequence[Dashboard]) -> list[Dashboard]:
    return sorted(dashboards, key=lambda d: (d.order, d.name.lower()))
