"""UI-level actions.

CEC key codes, keyboard keys and the carousel timer all funnel into this enum, so the
window never has to know where an action came from.
"""

from __future__ import annotations

from enum import StrEnum


class Action(StrEnum):
    MENU_TOGGLE = "menu.toggle"
    MENU_CLOSE = "menu.close"
    MENU_UP = "menu.up"
    MENU_DOWN = "menu.down"
    MENU_ACTIVATE = "menu.activate"

    DASHBOARD_NEXT = "dashboard.next"
    DASHBOARD_PREV = "dashboard.prev"
    DASHBOARD_RELOAD = "dashboard.reload"
    DASHBOARD_HOME = "dashboard.home"

    ROTATE_TOGGLE = "rotate.toggle"
    INFO_TOGGLE = "info.toggle"
    BLANK_ON = "blank.on"
    BLANK_OFF = "blank.off"


class DigitAction:
    """Jump directly to the Nth dashboard. Carries its digit, so not an enum member."""

    __slots__ = ("digit",)

    def __init__(self, digit: int) -> None:
        if not 0 <= digit <= 9:
            raise ValueError(f"digit out of range: {digit}")
        self.digit = digit

    def __eq__(self, other: object) -> bool:
        return isinstance(other, DigitAction) and other.digit == self.digit

    def __hash__(self) -> int:
        return hash(("digit", self.digit))

    def __repr__(self) -> str:
        return f"DigitAction({self.digit})"
