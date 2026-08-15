"""CEC backends and the CEC reader thread."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable

from .base import CecBackend, CecError, CecEvent, CecEventKind, NullCecBackend

log = logging.getLogger(__name__)

__all__ = [
    "CecBackend",
    "CecError",
    "CecEvent",
    "CecEventKind",
    "CecReader",
    "NullCecBackend",
    "create_backend",
]

RECONNECT_DELAY_SECONDS = 5.0

# How long a single poll() may block. This is a ceiling, not a heartbeat: the kernel
# backend returns the moment a CEC message arrives, and close() writes to its wake pipe
# so shutdown does not wait this out. It was 0.5s, which woke the CPU twice a second
# for the life of an appliance that is idle almost all of the time.
POLL_TIMEOUT_SECONDS = 5.0


def create_backend(backend: str, device: str, osd_name: str) -> CecBackend:
    """Build a backend by name. `auto` prefers the kernel adapter, then libcec."""
    if backend == "none":
        return NullCecBackend("cec-backend=none")
    if backend in ("kernel", "auto") and os.path.exists(device):
        from .kernel import KernelCecBackend

        return KernelCecBackend(device, osd_name)
    if backend == "kernel":
        return NullCecBackend(f"{device} not present")
    if backend in ("libcec", "auto"):
        from .libcec import LibCecBackend

        try:
            import cec  # noqa: F401  # type: ignore[import-not-found]
        except ImportError:
            if backend == "libcec":
                return NullCecBackend("python3-cec not available")
            return NullCecBackend(f"no CEC adapter ({device} missing, no libcec)")
        return LibCecBackend("", osd_name)
    return NullCecBackend(f"unknown backend {backend!r}")


class CecReader:
    """Runs a backend on its own thread and hands events to a callback.

    The callback runs on the reader thread — the UI layer is responsible for
    marshalling onto the main loop.
    """

    def __init__(self, backend: CecBackend, on_event: Callable[[CecEvent], None]) -> None:
        self._backend = backend
        self._on_event = on_event
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_open_error: str | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="cec-reader", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        try:
            self._backend.close()
        except Exception as exc:  # noqa: BLE001 - shutdown must not raise
            log.debug("error closing cec backend: %s", exc)
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._backend.open()
            except CecError as exc:
                # Loud once, then quiet until the reason changes. This retries every
                # five seconds for the life of an appliance that may run for months, so
                # an unconditional warning buries every other line in the log — and the
                # message that matters is the first one, which now carries the fix.
                detail = str(exc)
                if detail != self._last_open_error:
                    log.warning(
                        "cec unavailable (%s); retrying every %.0fs",
                        detail,
                        RECONNECT_DELAY_SECONDS,
                    )
                    self._last_open_error = detail
                else:
                    log.debug("cec still unavailable (%s)", detail)
                # Report it. create_backend() picks the kernel backend on nothing more
                # than os.path.exists(), so a device that is present but unopenable —
                # an unconnected hdmi-cec interface denies it with EPERM — left the
                # diagnostics panel showing "KernelCecBackend" and "/dev/cec0 present"
                # for an adapter that had never once opened. That reads as working CEC
                # and sent the search a long way in the wrong direction.
                self._emit(CecEvent(CecEventKind.ADAPTER_LOST, detail=str(exc)))
                if self._stop.wait(RECONNECT_DELAY_SECONDS):
                    return
                continue
            # Cleared on success so a later failure is reported loudly again rather
            # than being swallowed as a repeat of one from hours ago.
            self._last_open_error = None
            self._backend.announce()
            self._pump()

    def _pump(self) -> None:
        while not self._stop.is_set():
            try:
                events = self._backend.poll(POLL_TIMEOUT_SECONDS)
            except OSError as exc:
                log.warning("cec read failed (%s); reopening adapter", exc)
                self._safe_close()
                time.sleep(RECONNECT_DELAY_SECONDS)
                return
            for event in events:
                self._emit(event)

    def _emit(self, event: CecEvent) -> None:
        try:
            self._on_event(event)
        except Exception:  # noqa: BLE001 - a bad handler must not kill CEC
            log.exception("cec event handler raised")

    def _safe_close(self) -> None:
        try:
            self._backend.close()
        except Exception as exc:  # noqa: BLE001
            log.debug("error closing cec backend: %s", exc)
