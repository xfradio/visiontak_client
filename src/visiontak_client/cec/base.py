"""Backend-neutral CEC surface.

The kiosk only ever sees `CecEvent`s, so a kernel `/dev/cec0` adapter and a libcec
USB adapter are interchangeable.
"""

from __future__ import annotations

import abc
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
        self._stop = False

    def open(self) -> None:
        self._stop = False

    def poll(self, timeout: float) -> list[CecEvent]:
        import time

        if not self._stop:
            time.sleep(min(timeout, 1.0))
        return []

    def announce(self) -> None:
        return None

    def close(self) -> None:
        self._stop = True
