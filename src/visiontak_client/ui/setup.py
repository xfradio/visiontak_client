"""First-run setup: ask for the server address on-screen.

A display arrives on a wall with no keyboard-and-mouse workflow behind it, so the one
piece of configuration that cannot be guessed — where the server is — has to be
answerable from the couch. Everything else has a sane default.

Deliberately a single field. A setup flow with five questions on a television is a
flow nobody completes.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..branding import logo_path  # noqa: E402
from ..config import normalise_server_url  # noqa: E402

log = logging.getLogger(__name__)

LOGO_PX = 260
PLACEHOLDER = "https://visiontak.example"


class SetupScreen(Gtk.Box):
    """Logo, one field, one instruction."""

    def __init__(self, on_submit) -> None:  # noqa: ANN001 - callable(str) -> str | None
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        self._on_submit = on_submit

        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.add_css_class("vt-setup")

        path = logo_path()
        if path is not None:
            picture = Gtk.Picture.new_for_filename(str(path))
            picture.set_can_shrink(True)
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            picture.set_size_request(LOGO_PX, LOGO_PX)
            self.append(picture)

        heading = Gtk.Label(label="Where is your VisionTAK server?")
        heading.add_css_class("vt-setup-heading")
        self.append(heading)

        self._entry = Gtk.Entry()
        self._entry.set_placeholder_text(PLACEHOLDER)
        self._entry.set_width_chars(34)
        self._entry.set_alignment(0.5)
        self._entry.add_css_class("vt-setup-entry")
        self._entry.connect("activate", self._submit)
        self.append(self._entry)

        self._status = Gtk.Label(label="Type the address and press Enter")
        self._status.add_css_class("vt-setup-status")
        self.append(self._status)

        hint = Gtk.Label(label="A USB keyboard is needed for this step only")
        hint.add_css_class("vt-setup-hint")
        self.append(hint)

        # Say why automatic discovery did not answer. A field unit has no login, so
        # this line is the only way to tell "the network offered nothing" from "the
        # lease was unreadable" from "the option was malformed" without one.
        from ..discovery import describe

        try:
            detail = describe().detail
        except Exception:  # noqa: BLE001 - diagnostics must never block setup
            detail = ""
        if detail:
            diagnostic = Gtk.Label(label=detail)
            diagnostic.add_css_class("vt-setup-hint")
            self.append(diagnostic)

    def focus_entry(self) -> None:
        self._entry.grab_focus()

    def set_status(self, message: str, *, error: bool = False) -> None:
        self._status.set_label(message)
        if error:
            self._status.add_css_class("vt-setup-error")
        else:
            self._status.remove_css_class("vt-setup-error")

    def _submit(self, _entry) -> None:  # noqa: ANN001 - Gtk.Entry
        url = normalise_server_url(self._entry.get_text())
        if not url:
            self.set_status("That does not look like an address", error=True)
            return
        self.set_status(f"Saving {url}…")
        self._entry.set_sensitive(False)
        error = self._on_submit(url)
        if error:
            self._entry.set_sensitive(True)
            self.set_status(error, error=True)
        else:
            self.set_status("Saved — starting up…")
