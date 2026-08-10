"""CEC user control code -> kiosk action.

Codes are from CEC 1.4b "UI Command" (CEC Table 27). Remotes vary wildly in which
subset they emit, so the map is deliberately generous: several codes can produce the
same action rather than leaving a plausible button dead.
"""

from __future__ import annotations

from ..actions import Action, DigitAction

# Navigation
UI_SELECT = 0x00
UI_UP = 0x01
UI_DOWN = 0x02
UI_LEFT = 0x03
UI_RIGHT = 0x04
UI_ROOT_MENU = 0x09
UI_SETUP_MENU = 0x0A
UI_CONTENTS_MENU = 0x0B
UI_FAVORITE_MENU = 0x0C
UI_EXIT = 0x0D
UI_NUMBER_0 = 0x20
UI_NUMBER_9 = 0x29
UI_DOT = 0x2A
UI_ENTER = 0x2B
UI_CHANNEL_UP = 0x30
UI_CHANNEL_DOWN = 0x31
UI_PREVIOUS_CHANNEL = 0x32
UI_DISPLAY_INFORMATION = 0x35
UI_PAGE_UP = 0x37
UI_PAGE_DOWN = 0x38
UI_POWER = 0x40
UI_PLAY = 0x44
UI_STOP = 0x45
UI_PAUSE = 0x46
UI_REWIND = 0x48
UI_FAST_FORWARD = 0x49
UI_FORWARD = 0x4B
UI_BACKWARD = 0x4C
UI_POWER_OFF = 0x6C
UI_POWER_ON = 0x6D
UI_BLUE = 0x71
UI_RED = 0x72
UI_GREEN = 0x73
UI_YELLOW = 0x74

KEYMAP: dict[int, Action] = {
    UI_SELECT: Action.MENU_ACTIVATE,
    UI_ENTER: Action.MENU_ACTIVATE,
    UI_UP: Action.MENU_UP,
    UI_DOWN: Action.MENU_DOWN,
    UI_PAGE_UP: Action.MENU_UP,
    UI_PAGE_DOWN: Action.MENU_DOWN,
    UI_LEFT: Action.DASHBOARD_PREV,
    UI_RIGHT: Action.DASHBOARD_NEXT,
    UI_CHANNEL_DOWN: Action.DASHBOARD_PREV,
    UI_CHANNEL_UP: Action.DASHBOARD_NEXT,
    UI_BACKWARD: Action.DASHBOARD_PREV,
    UI_FORWARD: Action.DASHBOARD_NEXT,
    UI_REWIND: Action.DASHBOARD_PREV,
    UI_FAST_FORWARD: Action.DASHBOARD_NEXT,
    UI_EXIT: Action.MENU_CLOSE,
    UI_ROOT_MENU: Action.MENU_TOGGLE,
    UI_SETUP_MENU: Action.MENU_TOGGLE,
    UI_CONTENTS_MENU: Action.MENU_TOGGLE,
    UI_FAVORITE_MENU: Action.MENU_TOGGLE,
    UI_RED: Action.MENU_TOGGLE,
    UI_GREEN: Action.DASHBOARD_RELOAD,
    UI_YELLOW: Action.ROTATE_TOGGLE,
    UI_BLUE: Action.INFO_TOGGLE,
    UI_PLAY: Action.ROTATE_TOGGLE,
    UI_PAUSE: Action.ROTATE_TOGGLE,
    UI_STOP: Action.DASHBOARD_HOME,
    UI_DISPLAY_INFORMATION: Action.INFO_TOGGLE,
    UI_PREVIOUS_CHANNEL: Action.DASHBOARD_HOME,
    UI_POWER_OFF: Action.BLANK_ON,
    UI_POWER_ON: Action.BLANK_OFF,
}


def action_for(key_code: int) -> Action | DigitAction | None:
    """Resolve a CEC UI command. Returns None for codes the kiosk ignores."""
    if UI_NUMBER_0 <= key_code <= UI_NUMBER_9:
        return DigitAction(key_code - UI_NUMBER_0)
    return KEYMAP.get(key_code)


# Keyboard equivalents, so the kiosk is drivable over a USB keyboard during bring-up
# before CEC is wired. Keys are GDK key names.
KEYBOARD_MAP: dict[str, Action] = {
    "Up": Action.MENU_UP,
    "Down": Action.MENU_DOWN,
    "Left": Action.DASHBOARD_PREV,
    "Right": Action.DASHBOARD_NEXT,
    "Return": Action.MENU_ACTIVATE,
    "KP_Enter": Action.MENU_ACTIVATE,
    "space": Action.MENU_ACTIVATE,
    "Escape": Action.MENU_CLOSE,
    "BackSpace": Action.MENU_CLOSE,
    "m": Action.MENU_TOGGLE,
    "Menu": Action.MENU_TOGGLE,
    "r": Action.DASHBOARD_RELOAD,
    "F5": Action.DASHBOARD_RELOAD,
    "h": Action.DASHBOARD_HOME,
    "i": Action.INFO_TOGGLE,
    "p": Action.ROTATE_TOGGLE,
}


def action_for_key_name(name: str) -> Action | DigitAction | None:
    if len(name) == 1 and name.isdigit():
        return DigitAction(int(name))
    if name.startswith("KP_") and name[3:].isdigit():
        return DigitAction(int(name[3:]))
    return KEYBOARD_MAP.get(name)
