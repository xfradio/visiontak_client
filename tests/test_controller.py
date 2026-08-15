"""Controller behaviour that does not need a display.

These exist because controller.py used to import ui.app at module scope, which made
`gi` a hard requirement and left the enrolment and refresh logic untested — the same
blind spot that shipped a crash in the window constructor.
"""

from visiontak_client import controller as controller_module
from visiontak_client.api import Registration
from visiontak_client.config import Config
from visiontak_client.controller import KioskController


class FakeWindow:
    def __init__(self):
        self.configs = []
        self.statuses = []
        self.enrolment = []
        self.left_setup = False
        self.dashboards = []

    def set_config(self, config):
        self.configs.append(config)

    def setup_status(self, message, *, error=False):
        self.statuses.append((message, error))

    def set_enrolment_status(self, message):
        self.enrolment.append(message)

    def leave_setup(self):
        self.left_setup = True


def run_immediately(callback, *args, **kwargs):
    """Stand in for the GTK main loop: call it here and now."""
    callback(*args, **kwargs)


def make_controller(monkeypatch, config=None):
    monkeypatch.setattr(controller_module, "idle", run_immediately)
    controller = KioskController(config or Config(), client=object())
    window = FakeWindow()
    controller._window = window
    return controller, window


def test_the_window_is_told_the_address_typed_at_setup(monkeypatch):
    """The panel reported "Server (unset)" on a device already showing dashboards,
    because only the controller's copy of the config was replaced."""
    monkeypatch.setattr(controller_module, "VisionTakClient", lambda cfg: object())
    monkeypatch.setattr(controller_module.threading, "Thread", lambda **kw: _NoThread())
    controller, window = make_controller(monkeypatch)

    controller._configure_from_setup("http://10.1.1.146:3001")

    assert window.configs, "the window was never given the new config"
    assert window.configs[-1].server_url == "http://10.1.1.146:3001"


def test_the_window_is_told_the_issued_token(monkeypatch):
    """Approval replaces the config again; the window needs that one too."""
    approved = Config(server_url="http://vt.example", api_token="tok")
    monkeypatch.setattr(controller_module, "VisionTakClient", lambda cfg: object())
    monkeypatch.setattr(
        controller_module,
        "attempt_registration",
        lambda cfg: (Registration(status="approved", token="tok"), approved),
    )
    controller, window = make_controller(
        monkeypatch, Config(server_url="http://vt.example")
    )
    monkeypatch.setattr(controller, "_schedule_refresh", lambda delay: None)

    controller._poll_registration()

    assert window.configs[-1].api_token == "tok"


class _NoThread:
    """threading.Thread stand-in — the registration call is exercised directly."""

    def start(self):
        return None
