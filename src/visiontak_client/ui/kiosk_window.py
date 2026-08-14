"""The full-screen kiosk surface.

One Wayland surface, one Gtk.Stack of WebViews (one per dashboard, created lazily and
kept alive so switching is instant rather than a reload), plus the chooser overlay,
a status toast and a blanking layer.
"""

from __future__ import annotations

import logging
from pathlib import Path
from time import monotonic

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")  # imported lazily in _black() below
gi.require_version("WebKit", "6.0")
from gi.repository import GLib, Gtk, WebKit  # noqa: E402

from .. import __version__  # noqa: E402
from ..actions import Action, DigitAction  # noqa: E402
from ..branding import logo_path  # noqa: E402
from ..config import Config  # noqa: E402
from ..models import Dashboard  # noqa: E402
from ..netinfo import local_ip  # noqa: E402
from .menu import DashboardMenu  # noqa: E402
from .policy import HostAllowlist, make_policy_handler  # noqa: E402
from .setup import SetupScreen  # noqa: E402

log = logging.getLogger(__name__)

TOAST_TIMEOUT_SECONDS = 4
_SPLASH_LOGO_PX = 320
_IP_CACHE_SECONDS = 30


class KioskWindow(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application, config: Config) -> None:
        super().__init__(application=application, title="VisionTAK")
        self._config = config
        self._dashboards: list[Dashboard] = []
        self._views: dict[str, WebKit.WebView] = {}
        self._view_order: list[str] = []  # least- to most-recently used
        self._current = -1
        seed = [config.server_url] if config.server_url else []
        seed += [h.strip() for h in config.allowed_hosts.split(",") if h.strip()]
        self._allowlist = HostAllowlist(seed)
        if self._allowlist.allows_any:
            log.warning("allowed-hosts=* — navigation is unrestricted")
        self._toast_source = 0
        self._ip_cache: tuple[str, float] = ("", 0.0)
        self._handlers = self._build_handlers()
        self._session = _build_network_session(config)

        self.set_decorated(False)
        self.fullscreen()

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(180)
        self._placeholder, self._placeholder_label = _build_splash("Waiting for dashboards…")
        self._stack.add_named(self._placeholder, "__placeholder__")

        # A display with no server address configured cannot do anything useful, and
        # there is no other way to tell it one without a console. Ask on screen.
        # Set by the controller in attach(); the window has no business doing network
        # work itself, but it is where the address is typed.
        self.on_configured = None
        self._setup = SetupScreen(self._on_setup_submit)
        self._stack.add_named(self._setup, "__setup__")
        self._needs_setup = not config.server_url

        self._menu = DashboardMenu(self._on_menu_activate)
        self._toast = _build_toast()
        self._info = _build_info_panel()
        self._blank = _build_blank()
        # Covers the webview until its first paint, so a slow board shows the brand
        # rather than a white flash or a half-drawn dashboard.
        self._loading, _ = _build_splash("Connecting…")
        self._loading.set_visible(False)
        self._loading_ids: set[str] = set()

        overlay = Gtk.Overlay()
        overlay.set_child(self._stack)
        for widget in (self._info, self._menu, self._toast, self._loading, self._blank):
            overlay.add_overlay(widget)
        self.set_child(overlay)

        if self._needs_setup:
            log.info("no server-url configured — showing first-run setup")
            self._stack.set_visible_child(self._setup)
            self._setup.focus_entry()
            if config.dhcp_discovery:
                self._setup.show_discovery_detail()

    # -- dashboards --------------------------------------------------------

    @property
    def dashboards(self) -> list[Dashboard]:
        return list(self._dashboards)

    @property
    def current_dashboard(self) -> Dashboard | None:
        if 0 <= self._current < len(self._dashboards):
            return self._dashboards[self._current]
        return None

    def set_enrolment_status(self, message: str) -> None:
        """Replace the placeholder's caption while awaiting approval.

        Distinct from set_status, which reports CEC state into the diagnostics panel.

        A device sitting unapproved otherwise shows 'Waiting for dashboards…' forever,
        which sends whoever installed it looking for a fault that is not there.
        """
        self._placeholder_label.set_label(message or "Waiting for dashboards…")

    @property
    def setup_active(self) -> bool:
        """True while the setup field owns the keyboard, so key actions stand down."""
        return self._needs_setup

    def setup_status(self, message: str, *, error: bool = False) -> None:
        """Report progress on the setup screen, from any thread via idle()."""
        self._setup.set_status(message, error=error)
        if error:
            self._setup.reset_for_retry()

    def leave_setup(self) -> None:
        """Hand the screen back to the kiosk once an address is accepted."""
        if not self._needs_setup:
            return
        self._needs_setup = False
        self._stack.set_visible_child(self._placeholder)

    def _on_setup_submit(self, url: str) -> str | None:
        """Persist the address and enrol with it. Returns an error to show, or None.

        Registration happens here rather than being left to a daemon restart. The
        earlier version only wrote the setting and trusted the configure hook to
        restart us; when that did not happen the screen still said "Saved" and the
        device never appeared on the server, with nothing on screen to say so.
        """
        from ..config import persist

        try:
            persist("server-url", url)
        except Exception as exc:  # noqa: BLE001 - surfaced on screen, not swallowed
            log.error("could not save server-url: %s", exc)
            return f"Could not save: {exc}"
        log.info("server-url set to %s from the setup screen", url)

        if self.on_configured is None:
            # No controller attached (unit tests, --check-config). The setting is
            # saved; the next start will use it.
            return None
        self.on_configured(url)
        return None

    def set_dashboards(self, dashboards: list[Dashboard], *, keep_current: bool = True) -> None:
        if self._needs_setup:
            # Nothing the server says is interesting until it has been told where the
            # server is; leaving setup on screen beats flashing a placeholder over it.
            return
        previous = self.current_dashboard
        self._dashboards = dashboards
        for dashboard in dashboards:
            self._allowlist.allow(dashboard.url)
        self._menu.set_dashboards(dashboards)
        self._drop_stale_views({d.id for d in dashboards})

        if not dashboards:
            self._stack.set_visible_child(self._placeholder)
            self._current = -1
            self.toast("No dashboards available")
            return
        if keep_current and previous is not None:
            for index, dashboard in enumerate(dashboards):
                if dashboard.id == previous.id:
                    self.show_index(index)
                    return
        self.show_index(0)

    def _drop_stale_views(self, live_ids: set[str]) -> None:
        for dashboard_id in [k for k in self._views if k not in live_ids]:
            self._release_view(dashboard_id)

    def _release_view(self, dashboard_id: str) -> None:
        view = self._views.pop(dashboard_id, None)
        if view is None:
            return
        if dashboard_id in self._view_order:
            self._view_order.remove(dashboard_id)
        self._loading_ids.discard(dashboard_id)
        # Stop first: a view removed mid-load leaves its web process fetching.
        view.stop_loading()
        self._stack.remove(view)

    def show_index(self, index: int) -> None:
        if not self._dashboards:
            return
        index %= len(self._dashboards)
        dashboard = self._dashboards[index]
        self._current = index
        self._stack.set_visible_child(self._view_for(dashboard))
        self._sync_loading()
        self._update_info()

    def _view_for(self, dashboard: Dashboard) -> WebKit.WebView:
        view = self._views.get(dashboard.id)
        if view is not None:
            self._touch(dashboard.id)
            return view
        view = WebKit.WebView(network_session=self._session)
        view.set_settings(_build_web_settings(self._config))
        view.set_background_color(_black())
        view.connect("decide-policy", make_policy_handler(self._allowlist))
        view.connect("context-menu", lambda *_: True)  # no right-click menu on a kiosk
        view.connect("load-failed", self._on_load_failed)
        view.connect("load-changed", self._on_load_changed, dashboard.id)
        self._loading_ids.add(dashboard.id)
        view.load_uri(dashboard.url)
        self._views[dashboard.id] = view
        self._stack.add_named(view, dashboard.id)
        self._touch(dashboard.id)
        self._evict_surplus()
        return view

    def _touch(self, dashboard_id: str) -> None:
        """Mark a view as most recently used, for eviction ordering."""
        if dashboard_id in self._view_order:
            self._view_order.remove(dashboard_id)
        self._view_order.append(dashboard_id)

    def _evict_surplus(self) -> None:
        """Keep at most max_live_views WebViews alive, dropping least-recently-used.

        A hidden WebView is not idle: its web process keeps its render tree, timers and
        any animation the page runs. On a 1 GiB board that is what pushes the system
        into swap or the OOM killer, so trade a reload on return for the memory.
        """
        limit = max(1, self._config.max_live_views)
        current = self.current_dashboard
        current_id = current.id if current else None
        while len(self._views) > limit:
            victim = next(
                (i for i in self._view_order if i != current_id and i in self._views),
                None,
            )
            if victim is None:
                return
            log.info("evicting webview for %s (limit %d)", victim, limit)
            self._release_view(victim)

    def _on_load_changed(self, _view, event, dashboard_id: str) -> None:
        if event == WebKit.LoadEvent.STARTED:
            self._loading_ids.add(dashboard_id)
        elif event == WebKit.LoadEvent.FINISHED:
            self._loading_ids.discard(dashboard_id)
        self._sync_loading()

    def _sync_loading(self) -> None:
        dashboard = self.current_dashboard
        showing = dashboard is not None and dashboard.id in self._loading_ids
        self._loading.set_visible(showing)

    def _on_load_failed(self, _view, _event, uri: str, error) -> bool:
        log.warning("failed to load %s: %s", uri, getattr(error, "message", error))
        # Clear the splash even though WebKit normally still emits FINISHED after a
        # failure: if it does not, the cover stays up forever and the kiosk looks hung
        # rather than showing the error it is toasting about.
        dashboard = self.current_dashboard
        if dashboard is not None:
            self._loading_ids.discard(dashboard.id)
            self._sync_loading()
        self.toast(f"Could not load {_host_of(uri)} — retrying")
        return False

    def reload_current(self) -> None:
        dashboard = self.current_dashboard
        if dashboard is None:
            return
        view = self._views.get(dashboard.id)
        if view is not None:
            view.reload_bypass_cache()
            self.toast(f"Reloading {dashboard.name}")

    # -- actions -----------------------------------------------------------

    def handle_action(self, action: Action | DigitAction) -> None:
        if isinstance(action, DigitAction):
            self._jump_to_digit(action.digit)
            return
        handler = self._handlers.get(action)
        if handler is not None:
            handler()

    def _build_handlers(self) -> dict[Action, object]:
        """Action table, built once. It was rebuilt — twelve closures — per key press."""
        return {
            Action.MENU_TOGGLE: lambda: self._menu.toggle(max(self._current, 0)),
            Action.MENU_CLOSE: self._menu.close,
            Action.MENU_UP: lambda: self._menu_step(-1),
            Action.MENU_DOWN: lambda: self._menu_step(1),
            Action.MENU_ACTIVATE: self._activate,
            Action.DASHBOARD_NEXT: lambda: self._step(1),
            Action.DASHBOARD_PREV: lambda: self._step(-1),
            Action.DASHBOARD_RELOAD: self.reload_current,
            Action.DASHBOARD_HOME: lambda: self.show_index(0),
            Action.INFO_TOGGLE: self._toggle_info,
            Action.BLANK_ON: lambda: self.set_blanked(True),
            Action.BLANK_OFF: lambda: self.set_blanked(False),
        }

    def _menu_step(self, delta: int) -> None:
        """Up/down open the chooser if it is closed, rather than doing nothing."""
        if not self._menu.is_open:
            self._menu.open(max(self._current, 0))
            return
        self._menu.move(delta)

    def _activate(self) -> None:
        """OK confirms a highlighted entry, or opens the chooser if it is closed."""
        if self._menu.is_open:
            self._menu.activate_selected()
        else:
            self._menu.open(max(self._current, 0))

    def _step(self, delta: int) -> None:
        if self._menu.is_open:
            self._menu.move(delta)
            return
        if self._dashboards:
            self.show_index(self._current + delta)
            self.toast(self._dashboards[self._current].name)

    def _jump_to_digit(self, digit: int) -> None:
        index = (digit - 1) if digit > 0 else 9
        if index < len(self._dashboards):
            self._menu.close()
            self.show_index(index)
            self.toast(self._dashboards[index].name)

    def _on_menu_activate(self, index: int) -> None:
        self._menu.close()
        self.show_index(index)

    # -- chrome ------------------------------------------------------------

    def set_blanked(self, blanked: bool) -> None:
        self._blank.set_visible(blanked)

    def toast(self, message: str) -> None:
        self._toast.set_label(message)
        self._toast.set_visible(True)
        if self._toast_source:
            GLib.source_remove(self._toast_source)
        self._toast_source = GLib.timeout_add_seconds(TOAST_TIMEOUT_SECONDS, self._hide_toast)

    def _hide_toast(self) -> bool:
        self._toast.set_visible(False)
        self._toast_source = 0
        return GLib.SOURCE_REMOVE

    def _toggle_info(self) -> None:
        self._info.set_visible(not self._info.get_visible())
        self._update_info()

    def set_status(self, cec_status: str) -> None:
        self._cec_status = cec_status
        self._update_info()

    def _address(self) -> str:
        """This device's IP, cached briefly.

        local_ip() opens a UDP socket with a half-second timeout, and this runs on the
        GTK main loop. _update_info() fires on every dashboard change, so with the
        overlay open during a carousel rotation — and no network, which is exactly when
        somebody is reading this panel — each switch stalled the compositor's client
        for up to that timeout. An address does not change often enough to be worth it.
        """
        now = monotonic()
        value, taken_at = self._ip_cache
        if taken_at == 0.0 or now - taken_at > _IP_CACHE_SECONDS:
            value = local_ip(self._config.server_url)
            self._ip_cache = (value, now)
        return value

    def _update_info(self) -> None:
        if not self._info.get_visible():
            return
        dashboard = self.current_dashboard
        lines = [
            f"Device      {self._config.device_id}",
            # The only way to learn where to SSH to. A wall display shows nothing else
            # about itself, and every Ubuntu Core unit is called "localhost".
            f"Address     {self._address() or '(no network)'}",
            f"Server      {self._config.server_url or '(unset)'}",
            f"Dashboard   {dashboard.name if dashboard else '—'}"
            + (f"  [{self._current + 1}/{len(self._dashboards)}]" if dashboard else ""),
            f"CEC         {getattr(self, '_cec_status', 'starting')}",
            # The TV showing "raspberry" instead of this name means either the adapter
            # was never claimed (see the CEC line above) or the set cached the Pi
            # firmware's identity from before Linux started.
            f"OSD name    {self._config.osd_name}",
            f"CEC device  {self._config.cec_device} "
            f"({'present' if Path(self._config.cec_device).exists() else 'MISSING'})",
        ]
        self._info.set_label("\n".join(lines))


def _build_network_session(config: Config) -> WebKit.NetworkSession:
    data_dir = config.cache_dir / "webkit"
    data_dir.mkdir(parents=True, exist_ok=True)
    session = WebKit.NetworkSession.new(str(data_dir), str(data_dir / "cache"))
    session.set_itp_enabled(True)
    return session


def _accel_policy(mode: str):
    policy = WebKit.HardwareAccelerationPolicy
    if mode == "always":
        return policy.ALWAYS
    if mode == "never":
        return policy.NEVER
    # "auto" means "leave it to WebKit". WebKitGTK 6.0 removed ON_DEMAND — only ALWAYS
    # and NEVER survive — so resolve it at runtime instead of naming a member that may
    # not exist in the WebKit the device happens to ship.
    return getattr(policy, "ON_DEMAND", policy.ALWAYS)


_AVAILABLE_SETTINGS: set[str] | None = None


def _available_settings() -> set[str]:
    """Property names this WebKit build accepts, introspected once.

    list_properties() walks the whole GObject class every call, and settings are built
    per WebView — so this ran again for each dashboard the kiosk opened, to produce an
    answer that cannot change while the process lives.
    """
    global _AVAILABLE_SETTINGS
    if _AVAILABLE_SETTINGS is None:
        _AVAILABLE_SETTINGS = {p.name.replace("-", "_") for p in WebKit.Settings.list_properties()}
    return _AVAILABLE_SETTINGS


def _build_web_settings(config: Config) -> WebKit.Settings:
    wanted = {
        "enable_developer_extras": False,
        "enable_write_console_messages_to_stdout": False,
        "javascript_can_open_windows_automatically": False,
        "enable_back_forward_navigation_gestures": False,
        "enable_html5_database": True,
        "enable_html5_local_storage": True,
        "hardware_acceleration_policy": _accel_policy(config.hardware_acceleration),
        # Kiosk economies. A dashboard is never navigated back to and nothing here
        # captures a camera, so both are memory a 1 GiB board would rather spend on
        # the render tree.
        "enable_page_cache": False,
        "enable_media_stream": False,
        "enable_smooth_scrolling": False,
        # Autoplay. There is nobody in front of a wall display to click a video, so
        # requiring a gesture means embedded media simply never starts — which is what
        # happened to the YouTube tiles on a live dashboard.
        "media_playback_requires_user_gesture": False,
        # On only when we are not forcing the software path. Tying this to "always"
        # left WebGL off under the default "auto" as well, which silently broke any
        # dashboard embedding a map or a chart that needs it.
        "enable_webgl": config.hardware_acceleration != "never",
    }
    # WebKitGTK property sets differ between releases, and passing an unknown one to
    # the constructor is a hard TypeError at startup. The Pi's WebKit is not
    # necessarily the one this was written against, so filter to what exists.
    available = _available_settings()
    unknown = sorted(set(wanted) - available)
    if unknown:
        log.debug("WebKit build has no such settings, ignoring: %s", ", ".join(unknown))
    settings = WebKit.Settings(**{k: v for k, v in wanted.items() if k in available})

    # Append to WebKit's own user agent rather than replacing it. A bare
    # "VisionTAK-Kiosk" string matches no browser any site has heard of, and video
    # providers respond by refusing to serve a playable format at all — YouTube says
    # "your browser can't play this video" before codecs even come into it.
    settings.set_user_agent_with_application_details("VisionTAK-Kiosk", __version__)
    return settings


def _black():
    from gi.repository import Gdk

    return Gdk.RGBA(red=0.0, green=0.0, blue=0.0, alpha=1.0)


def _host_of(uri: str) -> str:
    from urllib.parse import urlparse

    return urlparse(uri).hostname or uri


def _build_splash(message: str) -> tuple[Gtk.Widget, Gtk.Label]:
    """Logo over a black field. Used for both 'no dashboards yet' and 'connecting'."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=28)
    box.set_halign(Gtk.Align.CENTER)
    box.set_valign(Gtk.Align.CENTER)
    box.add_css_class("vt-splash")

    path = logo_path()
    if path is not None:
        picture = Gtk.Picture.new_for_filename(str(path))
        picture.set_can_shrink(True)
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        picture.set_size_request(_SPLASH_LOGO_PX, _SPLASH_LOGO_PX)
        box.append(picture)

    label = Gtk.Label(label=message)
    label.add_css_class("vt-placeholder")
    label.set_justify(Gtk.Justification.CENTER)
    box.append(label)
    # The caption is returned too, so callers can update it in place — an unapproved
    # device needs to say why it is waiting.
    return box, label


def _build_toast() -> Gtk.Label:
    toast = Gtk.Label(label="")
    toast.add_css_class("vt-toast")
    toast.set_halign(Gtk.Align.CENTER)
    toast.set_valign(Gtk.Align.END)
    toast.set_visible(False)
    return toast


def _build_info_panel() -> Gtk.Label:
    info = Gtk.Label(label="")
    info.add_css_class("vt-info")
    info.set_halign(Gtk.Align.START)
    info.set_valign(Gtk.Align.START)
    info.set_xalign(0.0)
    info.set_visible(False)
    return info


def _build_blank() -> Gtk.Widget:
    blank = Gtk.Box()
    blank.add_css_class("vt-blank")
    blank.set_visible(False)
    return blank
