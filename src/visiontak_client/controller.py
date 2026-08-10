"""Wires CEC events, the config refresh loop and the carousel into the window.

Everything that touches GTK is marshalled onto the main loop; network work happens on
worker threads so a slow or dead server can never stall the compositor's client.
"""

from __future__ import annotations

import logging
import threading

from .actions import Action
from .api import ApiError, AuthError, ClientConfig, ConfigCache, VisionTakClient
from .cec import CecEvent, CecEventKind, CecReader, create_backend
from .cec.keymap import action_for
from .config import Config
from .ui.app import idle

log = logging.getLogger(__name__)

FIRST_REFRESH_DELAY_SECONDS = 1.0
AUTH_RETRY_SECONDS = 900


class KioskController:
    def __init__(self, config: Config, client: VisionTakClient | None = None) -> None:
        self._config = config
        self._client = client or VisionTakClient(config)
        self._cache = ConfigCache(config.cache_dir / "client-config.json")
        self._window = None
        self._reader: CecReader | None = None
        self._refresh_timer: threading.Timer | None = None
        self._rotate_source = 0
        self._rotating = bool(config.rotate_interval)
        self._server_default: str | None = None
        self._stopped = threading.Event()

    # -- lifecycle ---------------------------------------------------------

    def attach(self, window) -> None:  # noqa: ANN001 - KioskWindow, avoids import cycle
        self._window = window
        self._apply(self._cache.load(self._client.base_url), from_cache=True)
        self._start_cec()
        self._schedule_refresh(FIRST_REFRESH_DELAY_SECONDS)
        self._apply_rotation()

    def stop(self) -> None:
        self._stopped.set()
        if self._refresh_timer is not None:
            self._refresh_timer.cancel()
        if self._reader is not None:
            self._reader.stop()
        self._cancel_rotation()

    # -- config refresh ----------------------------------------------------

    def _schedule_refresh(self, delay: float) -> None:
        if self._stopped.is_set() or not self._config.refresh_interval:
            return
        self._refresh_timer = threading.Timer(delay, self._refresh)
        self._refresh_timer.name = "config-refresh"
        self._refresh_timer.daemon = True
        self._refresh_timer.start()

    def _refresh(self) -> None:
        next_delay = self._config.refresh_interval
        try:
            client_config = self._client.fetch_client_config()
        except AuthError as exc:
            # A rejected token will not fix itself; back off hard instead of
            # hammering the server every refresh interval.
            log.error("%s", exc)
            idle(self._window.toast, "Server rejected this device's token")
            next_delay = max(next_delay, AUTH_RETRY_SECONDS)
        except ApiError as exc:
            log.warning("config refresh failed: %s", exc)
            idle(self._window.toast, "Server unreachable — showing cached dashboards")
        else:
            log.info("server allows %d dashboard(s)", len(client_config.dashboards))
            self._cache.save(client_config)
            idle(self._apply, client_config, False)
        finally:
            self._schedule_refresh(next_delay)

    def _apply(self, client_config: ClientConfig, from_cache: bool) -> None:
        if from_cache and not client_config.dashboards:
            return
        had_none = not self._window.dashboards
        self._server_default = client_config.default_dashboard_id
        self._window.set_dashboards(client_config.dashboards)
        if from_cache:
            log.info("showing %d cached dashboard(s) while the server is contacted",
                     len(client_config.dashboards))
        if had_none:
            self._select_start_dashboard()

    def _select_start_dashboard(self) -> None:
        """Snap config wins over the server's defaultDashboardId; either may be unset."""
        wanted = self._config.start_dashboard or self._server_default
        if not wanted:
            return
        for index, dashboard in enumerate(self._window.dashboards):
            if dashboard.id == wanted:
                self._window.show_index(index)
                return
        log.warning("start dashboard %r is not in the allowed list", wanted)

    # -- CEC ---------------------------------------------------------------

    def _start_cec(self) -> None:
        backend = create_backend(
            self._config.cec_backend, self._config.cec_device, self._config.osd_name
        )
        self._reader = CecReader(backend, self._on_cec_event)
        self._reader.start()
        idle(self._window.set_status, type(backend).__name__)

    def _on_cec_event(self, event: CecEvent) -> None:
        """Runs on the CEC reader thread."""
        if event.kind is CecEventKind.KEY_PRESS and event.key_code is not None:
            action = action_for(event.key_code)
            if action is None:
                log.debug("unmapped CEC key 0x%02x", event.key_code)
                return
            log.debug("CEC key 0x%02x -> %s", event.key_code, action)
            idle(self._dispatch, action)
        elif event.kind is CecEventKind.STANDBY:
            idle(self._window.set_blanked, True)
        elif event.kind is CecEventKind.WAKE:
            idle(self._window.set_blanked, False)
        elif event.kind in (CecEventKind.ADAPTER_READY, CecEventKind.ADAPTER_LOST):
            idle(self._window.set_status, f"{event.kind.value} ({event.detail})")

    # -- actions -----------------------------------------------------------

    def _dispatch(self, action) -> None:  # noqa: ANN001 - Action | DigitAction
        if action is Action.ROTATE_TOGGLE:
            self._toggle_rotation()
            return
        # Any manual navigation restarts the carousel clock so a viewer's choice is
        # not yanked away half a second later.
        self._restart_rotation_clock()
        self._window.handle_action(action)

    # -- carousel ----------------------------------------------------------

    def _apply_rotation(self) -> None:
        self._cancel_rotation()
        if not (self._rotating and self._config.rotate_interval):
            return
        from gi.repository import GLib

        self._rotate_source = GLib.timeout_add_seconds(self._config.rotate_interval, self._rotate)

    def _rotate(self) -> bool:
        self._window.handle_action(Action.DASHBOARD_NEXT)
        return True  # GLib.SOURCE_CONTINUE

    def _cancel_rotation(self) -> None:
        if self._rotate_source:
            from gi.repository import GLib

            GLib.source_remove(self._rotate_source)
            self._rotate_source = 0

    def _restart_rotation_clock(self) -> None:
        if self._rotate_source:
            self._apply_rotation()

    def _toggle_rotation(self) -> None:
        if not self._config.rotate_interval:
            self._window.toast("Carousel is not configured (rotate-interval=0)")
            return
        self._rotating = not self._rotating
        self._apply_rotation()
        self._window.toast("Carousel on" if self._rotating else "Carousel paused")
