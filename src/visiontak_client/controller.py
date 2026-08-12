"""Wires CEC events, the config refresh loop and the carousel into the window.

Everything that touches GTK is marshalled onto the main loop; network work happens on
worker threads so a slow or dead server can never stall the compositor's client.
"""

from __future__ import annotations

import dataclasses
import logging
import threading

from .actions import Action
from .api import ApiError, AuthError, ClientConfig, ConfigCache, VisionTakClient
from .cec import CecEvent, CecEventKind, CecReader, create_backend
from .cec.keymap import action_for
from .config import Config
from .firstrun import POLL_SECONDS, attempt_registration
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
        self._registration_timer: threading.Timer | None = None
        self._rotate_source = 0
        self._rotating = bool(config.rotate_interval)
        self._server_default: str | None = None
        self._stopped = threading.Event()

    # -- lifecycle ---------------------------------------------------------

    def attach(self, window) -> None:  # noqa: ANN001 - KioskWindow, avoids import cycle
        self._window = window
        window.on_configured = self._configure_from_setup
        self._apply(self._cache.load(self._client.base_url), from_cache=True)
        self._start_cec()
        self._start_registration()
        self._schedule_refresh(FIRST_REFRESH_DELAY_SECONDS)
        self._apply_rotation()

    # -- enrolment ---------------------------------------------------------

    def _configure_from_setup(self, url: str) -> None:
        """Apply an address typed on the setup screen and enrol with it now.

        On a worker thread: registration is a network call, and blocking the GTK loop
        would freeze the very screen that is meant to report progress.
        """
        self._config = dataclasses.replace(self._config, server_url=url)
        self._client = VisionTakClient(self._config)
        idle(self._window.setup_status, "Registering with the server…")
        threading.Thread(
            target=self._register_from_setup, name="setup-register", daemon=True
        ).start()

    def _register_from_setup(self) -> None:
        result, config = attempt_registration(self._config)
        self._config = config

        if result is None:
            # The address saved, but nothing answered. Stay on setup so the address
            # can be corrected rather than leaving a blank screen behind.
            idle(
                self._window.setup_status,
                f"Saved, but {self._config.server_url} did not answer",
                error=True,
            )
            return

        if result.approved and config.api_token:
            self._client = VisionTakClient(config)
            idle(self._window.setup_status, "Approved — starting up")
            idle(self._window.leave_setup)
            self._schedule_refresh(0.5)
            return

        idle(self._window.setup_status, "Registered — waiting for approval")
        idle(self._window.leave_setup)
        idle(self._window.set_enrolment_status, "Waiting for approval on the server")
        self._start_registration()

    def _start_registration(self) -> None:
        """Enrol, and keep asking while the server says pending.

        A new device stays pending until an admin approves it, which can be days and
        several reboots away. Polling in the background means the display picks the
        token up on its own the moment that happens, rather than needing a power cycle
        timed to the approval.
        """
        if not self._config.server_url or self._config.api_token:
            return
        self._registration_timer = threading.Timer(0.5, self._poll_registration)
        self._registration_timer.name = "registration"
        self._registration_timer.daemon = True
        self._registration_timer.start()

    def _poll_registration(self) -> None:
        if self._stopped.is_set():
            return
        result, config = attempt_registration(self._config)
        self._config = config

        if result is not None and result.approved and config.api_token:
            log.info("approved — reloading with the issued token")
            self._client = VisionTakClient(config)
            idle(self._window.set_enrolment_status, "")
            self._schedule_refresh(0.5)
            return

        if result is not None and result.pending:
            idle(self._window.set_enrolment_status, "Waiting for approval on the server")
        elif result is not None and result.approved:
            # Approved with no token, and we have none: an admin has to re-issue.
            idle(self._window.set_enrolment_status, "Approved, but no token was issued")

        self._registration_timer = threading.Timer(POLL_SECONDS, self._poll_registration)
        self._registration_timer.name = "registration"
        self._registration_timer.daemon = True
        self._registration_timer.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._refresh_timer is not None:
            self._refresh_timer.cancel()
        if self._registration_timer is not None:
            self._registration_timer.cancel()
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
        # Report *why* CEC is inactive, not just that it is. The fallback carries a
        # reason ("/dev/cec0 missing" is the usual one, meaning the hdmi-cec interface
        # is not connected) and dropping it left the panel saying NullCecBackend with
        # no way to tell a missing device from a missing interface from a wrong
        # setting — on a unit with no login.
        detail = getattr(backend, "reason", "") or ""
        name = type(backend).__name__
        log.info("CEC backend: %s%s", name, f" ({detail})" if detail else "")
        idle(self._window.set_status, f"{name} ({detail})" if detail else name)

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
