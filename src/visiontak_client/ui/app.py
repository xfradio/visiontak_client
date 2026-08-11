"""GTK application shell.

Wayland only. If GDK cannot reach a Wayland compositor we fail loudly rather than
silently falling back to X11 — falling back would quietly discard the isolation
guarantees that motivated Mir/Ubuntu Frame in the first place.
"""

from __future__ import annotations

import logging
import os
from importlib import resources

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from ..cec.keymap import action_for_key_name  # noqa: E402
from ..config import Config  # noqa: E402
from .kiosk_window import KioskWindow  # noqa: E402

log = logging.getLogger(__name__)

APP_ID = "io.visiontak.Client"


def enforce_wayland() -> None:
    os.environ["GDK_BACKEND"] = "wayland"
    os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")
    # WebKitGTK's bubblewrap sandbox cannot nest inside snap confinement; the snap's
    # own AppArmor/seccomp profile is the enclosing boundary instead.
    os.environ.setdefault("WEBKIT_FORCE_SANDBOX", "0")


class KioskApplication(Gtk.Application):
    def __init__(self, config: Config) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self._config = config
        self.window: KioskWindow | None = None
        self._controller = None

    def set_controller(self, controller) -> None:  # noqa: ANN001 - avoids import cycle
        self._controller = controller

    def do_startup(self) -> None:  # noqa: N802 - GObject vfunc name
        Gtk.Application.do_startup(self)
        _load_css()

    def do_activate(self) -> None:  # noqa: N802 - GObject vfunc name
        if self.window is None:
            self.window = KioskWindow(self, self._config)
            self._install_key_handler(self.window)
            if self._controller is not None:
                self._controller.attach(self.window)
        self.window.present()

    def _install_key_handler(self, window: KioskWindow) -> None:
        controller = Gtk.EventControllerKey()
        controller.connect("key-pressed", self._on_key_pressed)
        window.add_controller(controller)

    def _on_key_pressed(self, _controller, keyval: int, _keycode: int, _state) -> bool:
        # During setup the text field owns the keyboard: "h" is a character being
        # typed into a hostname, not the home-dashboard action.
        if self.window is not None and self.window.setup_active:
            return False
        name = Gdk.keyval_name(keyval) or ""
        action = action_for_key_name(name)
        if action is None:
            return False
        if self.window is not None:
            self.window.handle_action(action)
        return True


def _load_css() -> None:
    provider = Gtk.CssProvider()
    css = resources.files("visiontak_client.ui").joinpath("style.css").read_bytes()
    provider.load_from_data(css)
    display = Gdk.Display.get_default()
    if display is None:
        raise RuntimeError(
            "no display: is Ubuntu Frame running and WAYLAND_DISPLAY set? "
            f"(WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY')!r}, "
            f"XDG_RUNTIME_DIR={os.environ.get('XDG_RUNTIME_DIR')!r})"
        )
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


def idle(callback, *args) -> None:
    """Marshal a call from a worker thread onto the GTK main loop."""
    GLib.idle_add(lambda: (callback(*args), GLib.SOURCE_REMOVE)[1])
