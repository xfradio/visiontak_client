"""Backend-neutral CEC surface.

The kiosk only ever sees `CecEvent`s, so a kernel `/dev/cec0` adapter and a libcec
USB adapter are interchangeable.
"""

from __future__ import annotations

import abc
import threading
from dataclasses import dataclass
from enum import StrEnum


class CecEventKind(StrEnum):
    KEY_PRESS = "key.press"
    KEY_RELEASE = "key.release"
    STANDBY = "standby"
    WAKE = "wake"
    ACTIVE_SOURCE_LOST = "active_source.lost"
    ADAPTER_READY = "adapter.ready"
    ADAPTER_LOST = "adapter.lost"


@dataclass(frozen=True)
class CecEvent:
    kind: CecEventKind
    #: CEC user control code for KEY_* events, otherwise None.
    key_code: int | None = None
    detail: str = ""


class CecError(RuntimeError):
    pass


class CecBackend(abc.ABC):
    """A CEC adapter.

    Implementations must be safe to `close()` from another thread while `poll()` is
    blocked, so the kiosk can shut down promptly.
    """

    @abc.abstractmethod
    def open(self) -> None: ...

    @abc.abstractmethod
    def poll(self, timeout: float) -> list[CecEvent]:
        """Block up to `timeout` seconds and return whatever arrived (possibly empty)."""

    @abc.abstractmethod
    def announce(self) -> None:
        """Ask the TV to power on and switch its input to us."""

    @abc.abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> CecBackend:
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class NullCecBackend(CecBackend):
    """Used when CEC is disabled or no adapter is present. Keeps the kiosk running."""

    def __init__(self, reason: str = "cec disabled") -> None:
        self.reason = reason
        self._stop = threading.Event()

    def open(self) -> None:
        self._stop.clear()

    def poll(self, timeout: float) -> list[CecEvent]:
        # An Event rather than time.sleep, for two reasons. It honours the caller's
        # timeout instead of capping at one second, so a device with no CEC at all —
        # the common case on a stock gadget — stops waking the CPU once a second for
        # the life of the unit. And close() interrupts it, so shutdown no longer waits
        # out the remainder of a sleep it cannot cancel.
        self._stop.wait(timeout)
        return []

    def announce(self) -> None:
        return None

    def close(self) -> None:
        self._stop.set()
