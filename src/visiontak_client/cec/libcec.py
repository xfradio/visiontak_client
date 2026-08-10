"""libcec backend for USB adapters (Pulse-Eight and friends).

Not the default path on Raspberry Pi — the kernel adapter is better in every way there
— but x86 boxes without a CEC-capable GPU need a USB dongle, and those are driven by
libcec. Kept thin: it maps libcec callbacks onto the same `CecEvent` stream.

Requires the `cec` Python bindings (Debian: `python3-cec`, shipped via stage-packages
when the snap is built with `CEC_BACKEND=libcec`).
"""

from __future__ import annotations

import logging
import queue
import threading

from .base import CecBackend, CecError, CecEvent, CecEventKind

log = logging.getLogger(__name__)


class LibCecBackend(CecBackend):
    def __init__(self, device: str = "", osd_name: str = "VisionTAK") -> None:
        self._device = device
        self._osd_name = osd_name[:14]
        self._events: queue.Queue[CecEvent] = queue.Queue()
        self._cec = None
        self._closed = threading.Event()

    def open(self) -> None:
        try:
            import cec  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on image contents
            raise CecError("python3-cec is not available in this build") from exc
        self._cec = cec
        cec.add_callback(self._on_key, cec.EVENT_KEYPRESS)
        cec.add_callback(self._on_command, cec.EVENT_COMMAND)
        try:
            if self._device:
                cec.init(self._device)
            else:
                cec.init()
        except Exception as exc:  # libcec raises bare Exception
            raise CecError(f"libcec init failed: {exc}") from exc
        self._closed.clear()
        log.info("libcec backend ready (device=%s)", self._device or "auto")

    def _on_key(self, _event: int, key: int, duration: int) -> None:
        kind = CecEventKind.KEY_RELEASE if duration else CecEventKind.KEY_PRESS
        self._events.put(CecEvent(kind, key_code=key))

    def _on_command(self, _event: int, command: str) -> None:
        # libcec hands commands over as "0f:36"-style hex strings.
        parts = command.split(":")
        if len(parts) >= 2 and parts[1].lower() == "36":
            self._events.put(CecEvent(CecEventKind.STANDBY, detail="tv standby"))

    def poll(self, timeout: float) -> list[CecEvent]:
        out: list[CecEvent] = []
        try:
            out.append(self._events.get(timeout=timeout))
        except queue.Empty:
            return out
        while True:
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                return out

    def announce(self) -> None:
        if self._cec is None:
            return
        try:
            self._cec.set_active_source()
        except Exception as exc:  # pragma: no cover
            log.warning("libcec set_active_source failed: %s", exc)

    def close(self) -> None:
        self._closed.set()
        self._events.put(CecEvent(CecEventKind.ADAPTER_LOST, detail="closing"))
        self._cec = None
