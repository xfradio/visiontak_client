"""The dashboard chooser overlay.

Driven entirely by discrete actions (up / down / activate) because the only input
device in the field is a TV remote — there is no pointer to hover with.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..models import Dashboard  # noqa: E402


class DashboardMenu(Gtk.Revealer):
    def __init__(self, on_activate: Callable[[int], None]) -> None:
        super().__init__()
        self._on_activate = on_activate
        self._dashboards: list[Dashboard] = []
        self._selected = 0

        self.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        self.set_transition_duration(150)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.set_reveal_child(False)

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        panel.add_css_class("vt-menu")

        heading = Gtk.Label(label="Dashboards")
        heading.add_css_class("vt-menu-title")
        heading.set_xalign(0.0)
        panel.append(heading)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.add_css_class("vt-menu-list")
        self._list.connect("row-activated", self._on_row_activated)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(320)
        scroller.set_child(self._list)
        panel.append(scroller)

        hint = Gtk.Label(label="▲▼ choose   OK select   BACK close   1–9 jump")
        hint.add_css_class("vt-menu-hint")
        panel.append(hint)

        self.set_child(panel)

    # -- state -------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self.get_reveal_child()

    @property
    def selected_index(self) -> int:
        return self._selected

    def set_dashboards(self, dashboards: Sequence[Dashboard]) -> None:
        self._dashboards = list(dashboards)
        while (row := self._list.get_row_at_index(0)) is not None:
            self._list.remove(row)
        for index, dashboard in enumerate(self._dashboards, start=1):
            self._list.append(_build_row(index, dashboard))
        self.select(min(self._selected, max(len(self._dashboards) - 1, 0)))

    def open(self, current_index: int) -> None:
        if not self._dashboards:
            return
        self.select(current_index)
        self.set_reveal_child(True)
        self.grab_focus()

    def close(self) -> None:
        self.set_reveal_child(False)

    def toggle(self, current_index: int) -> None:
        self.close() if self.is_open else self.open(current_index)

    def move(self, delta: int) -> None:
        if not self._dashboards:
            return
        self.select((self._selected + delta) % len(self._dashboards))

    def select(self, index: int) -> None:
        if not self._dashboards:
            return
        self._selected = max(0, min(index, len(self._dashboards) - 1))
        row = self._list.get_row_at_index(self._selected)
        if row is not None:
            self._list.select_row(row)
            row.grab_focus()

    def activate_selected(self) -> None:
        if self._dashboards:
            self._on_activate(self._selected)

    def _on_row_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        self._on_activate(row.get_index())


def _build_row(number: int, dashboard: Dashboard) -> Gtk.ListBoxRow:
    row = Gtk.ListBoxRow()
    row.add_css_class("vt-menu-row")

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)

    badge = Gtk.Label(label=str(number) if number <= 9 else "•")
    badge.add_css_class("vt-menu-badge")
    box.append(badge)

    labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    title = Gtk.Label(label=dashboard.name)
    title.set_xalign(0.0)
    title.add_css_class("vt-menu-name")
    labels.append(title)
    if dashboard.group:
        subtitle = Gtk.Label(label=dashboard.group)
        subtitle.set_xalign(0.0)
        subtitle.add_css_class("vt-menu-group")
        labels.append(subtitle)
    box.append(labels)

    row.set_child(box)
    return row
