import pytest

from visiontak_client.actions import Action, DigitAction
from visiontak_client.cec import keymap


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (keymap.UI_UP, Action.MENU_UP),
        (keymap.UI_DOWN, Action.MENU_DOWN),
        (keymap.UI_SELECT, Action.MENU_ACTIVATE),
        (keymap.UI_EXIT, Action.MENU_CLOSE),
        (keymap.UI_LEFT, Action.DASHBOARD_PREV),
        (keymap.UI_RIGHT, Action.DASHBOARD_NEXT),
        (keymap.UI_ROOT_MENU, Action.MENU_TOGGLE),
        (keymap.UI_GREEN, Action.DASHBOARD_RELOAD),
    ],
)
def test_core_navigation_codes(code, expected):
    assert keymap.action_for(code) is expected


def test_number_keys_become_digit_actions():
    assert keymap.action_for(keymap.UI_NUMBER_0) == DigitAction(0)
    assert keymap.action_for(keymap.UI_NUMBER_0 + 5) == DigitAction(5)
    assert keymap.action_for(keymap.UI_NUMBER_9) == DigitAction(9)


def test_unmapped_codes_are_ignored():
    assert keymap.action_for(0x7F) is None


def test_digit_range_is_enforced():
    with pytest.raises(ValueError):
        DigitAction(10)


def test_keyboard_fallback_matches_cec_semantics():
    assert keymap.action_for_key_name("Up") is Action.MENU_UP
    assert keymap.action_for_key_name("Escape") is Action.MENU_CLOSE
    assert keymap.action_for_key_name("3") == DigitAction(3)
    assert keymap.action_for_key_name("KP_7") == DigitAction(7)
    assert keymap.action_for_key_name("Hyper_L") is None


def test_no_cec_code_maps_to_two_actions_by_accident():
    """Guards against a copy/paste duplicate silently overwriting an earlier entry."""
    assert len(keymap.KEYMAP) == len(set(keymap.KEYMAP))
