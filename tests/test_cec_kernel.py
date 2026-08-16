import time

import pytest

pytest.importorskip("fcntl", reason="kernel CEC backend is Linux-only")

from visiontak_client.cec import uapi as u  # noqa: E402
from visiontak_client.cec.base import CecEventKind  # noqa: E402
from visiontak_client.cec.kernel import KernelCecBackend  # noqa: E402


def make_msg(opcode: int, operands: bytes = b"", initiator: int = 0, destination: int = 4):
    msg = u.CecMsg()
    payload = bytes([(initiator << 4) | destination, opcode]) + operands
    msg.len = len(payload)
    for index, byte in enumerate(payload):
        msg.msg[index] = byte
    return msg


@pytest.fixture
def backend():
    b = KernelCecBackend("/dev/null", "VisionTAK")
    b._phys_addr = 0x1000
    b._log_addr = 4
    return b


def test_user_control_pressed_carries_the_key_code(backend):
    event = backend._decode(make_msg(u.CEC_MSG_USER_CONTROL_PRESSED, b"\x01"))
    assert event.kind is CecEventKind.KEY_PRESS
    assert event.key_code == 0x01


def test_user_control_pressed_without_an_operand_is_ignored(backend):
    assert backend._decode(make_msg(u.CEC_MSG_USER_CONTROL_PRESSED)) is None


def test_standby_and_wake(backend):
    assert backend._decode(make_msg(u.CEC_MSG_STANDBY)).kind is CecEventKind.STANDBY
    assert backend._decode(make_msg(u.CEC_MSG_IMAGE_VIEW_ON)).kind is CecEventKind.WAKE


def test_set_stream_path_to_us_is_a_wake(backend, monkeypatch):
    announced = []
    monkeypatch.setattr(backend, "announce", lambda: announced.append(True))
    event = backend._decode(make_msg(u.CEC_MSG_SET_STREAM_PATH, b"\x10\x00"))
    assert event.kind is CecEventKind.WAKE
    assert announced == [True]


def test_set_stream_path_elsewhere_means_we_lost_the_input(backend):
    event = backend._decode(make_msg(u.CEC_MSG_SET_STREAM_PATH, b"\x20\x00"))
    assert event.kind is CecEventKind.ACTIVE_SOURCE_LOST


def test_another_active_source_means_we_lost_the_input(backend):
    event = backend._decode(make_msg(u.CEC_MSG_ACTIVE_SOURCE, b"\x30\x00"))
    assert event.kind is CecEventKind.ACTIVE_SOURCE_LOST


def test_our_own_active_source_echo_is_not_a_loss(backend):
    assert backend._decode(make_msg(u.CEC_MSG_ACTIVE_SOURCE, b"\x10\x00")) is None


def test_unhandled_opcodes_are_dropped(backend):
    assert backend._decode(make_msg(0x9E)) is None


def test_hdmi_unplug_clears_the_logical_address(backend):
    events = backend._on_state_change(u.CEC_PHYS_ADDR_INVALID)
    assert events[0].kind is CecEventKind.ADAPTER_LOST
    assert backend._log_addr == u.CEC_LOG_ADDR_UNREGISTERED


def test_osd_name_is_truncated_to_the_cec_limit():
    assert KernelCecBackend("/dev/null", "VisionTAK Operations")._osd_name == b"VisionTAK Oper"


def test_announce_tells_the_tv_our_osd_name(backend, monkeypatch):
    """The TV's source list should read VisionTAK without having to ask for it."""
    sent = []
    monkeypatch.setattr(
        backend,
        "_transmit",
        lambda dst, op, operands=b"": sent.append((dst, op, operands)),
    )
    backend.announce()
    assert (u.CEC_LOG_ADDR_TV, u.CEC_MSG_SET_OSD_NAME, b"VisionTAK") in sent


def test_no_osd_name_is_not_transmitted(monkeypatch):
    backend = KernelCecBackend("/dev/null", "")
    backend._phys_addr = 0x1000
    backend._log_addr = 4
    sent = []
    monkeypatch.setattr(backend, "_transmit", lambda dst, op, operands=b"": sent.append(op))
    backend.send_osd_name()
    assert u.CEC_MSG_SET_OSD_NAME not in sent


def test_the_null_backend_wakes_on_close():
    """close() must interrupt a poll, or shutdown waits out the full timeout."""
    from visiontak_client.cec.base import NullCecBackend

    backend = NullCecBackend()
    backend.open()
    backend.close()
    started = time.monotonic()
    assert backend.poll(30.0) == []
    assert time.monotonic() - started < 1.0, "poll ignored the close and slept"


def test_the_null_backend_waits_when_running():
    """It must still block, or the reader thread becomes a busy loop."""
    from visiontak_client.cec.base import NullCecBackend

    backend = NullCecBackend()
    backend.open()
    started = time.monotonic()
    backend.poll(0.2)
    assert time.monotonic() - started >= 0.15


def test_a_deferred_logical_address_is_retried(monkeypatch):
    """A link already up before the daemon started emits no state change, so the
    adapter would otherwise sit open and unregistered forever."""
    backend = KernelCecBackend.__new__(KernelCecBackend)
    backend._fd = 3
    backend._log_addr = u.CEC_LOG_ADDR_UNREGISTERED
    backend._phys_addr = u.CEC_PHYS_ADDR_INVALID
    backend._osd_name = b"VisionTAK"

    # The TV has since supplied an address; the state-change event never arrived.
    monkeypatch.setattr(backend, "_refresh_phys_addr", lambda: setattr_and_return(backend, 0x1000))
    monkeypatch.setattr(backend, "_claim_logical_address", lambda: claim(backend))
    monkeypatch.setattr(backend, "announce", lambda: None)

    events = backend._retry_claim()
    assert [e.kind for e in events] == [CecEventKind.ADAPTER_READY]


def setattr_and_return(backend, value):
    backend._phys_addr = value
    return value


def claim(backend):
    backend._log_addr = 4
    return True


def test_no_retry_once_registered(monkeypatch):
    """The retry must not re-announce on every idle poll."""
    backend = KernelCecBackend.__new__(KernelCecBackend)
    backend._log_addr = 4
    called = []
    monkeypatch.setattr(backend, "_refresh_phys_addr", lambda: called.append(1))
    assert backend._retry_claim() == []
    assert not called, "re-read the physical address while already registered"


def busy_backend(monkeypatch, existing):
    """A backend whose S_LOG_ADDRS is refused with EBUSY, with `existing` as whatever
    G_LOG_ADDRS then reports the adapter is already configured with."""
    import errno

    backend = KernelCecBackend.__new__(KernelCecBackend)
    backend._device = "/dev/cec0"
    backend._fd = 3
    backend._osd_name = b"VisionTAK"
    backend._log_addr = u.CEC_LOG_ADDR_UNREGISTERED
    backend._phys_addr = 0x1000

    def fake_ioctl(request, payload):
        if request == u.CEC_ADAP_S_LOG_ADDRS:
            raise OSError(errno.EBUSY, "Device or resource busy")
        assert request == u.CEC_ADAP_G_LOG_ADDRS
        payload.num_log_addrs = existing[0]
        payload.log_addr[0] = existing[1]
        return payload

    monkeypatch.setattr(backend, "_ioctl", fake_ioctl)
    return backend


def test_a_busy_adapter_adopts_the_address_it_already_has(monkeypatch):
    """The logical-address config outlives the file handle that set it, so our own
    first claim makes every later one EBUSY. Treating that as "try again later" left
    the client unregistered forever on an adapter that was working."""
    backend = busy_backend(monkeypatch, existing=(1, 4))
    assert backend._claim_logical_address() is True
    assert backend._log_addr == 4


def test_a_busy_adapter_with_no_configuration_is_not_adopted(monkeypatch):
    backend = busy_backend(monkeypatch, existing=(0, u.CEC_LOG_ADDR_UNREGISTERED))
    assert backend._claim_logical_address() is False
    assert backend._log_addr == u.CEC_LOG_ADDR_UNREGISTERED


def test_a_busy_adapter_registered_as_unregistered_is_not_adopted(monkeypatch):
    """num_log_addrs is set but the negotiation landed nowhere — adopting 15 would
    make _transmit believe it had an address and send from the broadcast one."""
    backend = busy_backend(monkeypatch, existing=(1, u.CEC_LOG_ADDR_UNREGISTERED))
    assert backend._claim_logical_address() is False
    assert backend._log_addr == u.CEC_LOG_ADDR_UNREGISTERED


def test_a_busy_adapter_reporting_an_invalid_address_is_not_adopted(monkeypatch):
    """0xff is what the kernel reports for a slot with no address, and it is not 15.
    It passed the UNREGISTERED check, was adopted, and then could not fit in the four
    header bits — bytes() raised ValueError, which is not OSError, so it went past
    every handler and killed the CEC thread on the first announce."""
    backend = busy_backend(monkeypatch, existing=(1, u.CEC_LOG_ADDR_INVALID))
    assert backend._claim_logical_address() is False
    assert backend._log_addr == u.CEC_LOG_ADDR_UNREGISTERED


def test_transmit_refuses_an_unaddressable_initiator(monkeypatch):
    """The last line of defence: whatever put it there, an address that cannot go in a
    header must not be built into one."""
    backend = KernelCecBackend.__new__(KernelCecBackend)
    backend._fd = 3
    backend._log_addr = u.CEC_LOG_ADDR_INVALID
    sent = []
    monkeypatch.setattr(backend, "_ioctl", lambda request, payload: sent.append(payload))

    backend._transmit(u.CEC_LOG_ADDR_TV, u.CEC_MSG_IMAGE_VIEW_ON)

    assert sent == [], "transmitted from an address that does not fit a header"


def test_eperm_names_the_unconnected_plug_and_the_fix():
    """EPERM is the device cgroup, not file permissions. Reporting it bare sent one
    investigation after group membership, which cannot matter — the daemon is root."""
    import errno

    from visiontak_client.cec.kernel import CONNECT_COMMAND, _open_hint

    hint = _open_hint(OSError(errno.EPERM, "Operation not permitted"))
    assert "not connected" in hint
    assert CONNECT_COMMAND in hint


def test_eacces_points_at_permissions_instead():
    import errno

    from visiontak_client.cec.kernel import _open_hint

    hint = _open_hint(OSError(errno.EACCES, "Permission denied"))
    assert "file permissions" in hint
    assert "snap connect" not in hint


def test_a_missing_device_points_at_the_display_driver():
    import errno

    from visiontak_client.cec.kernel import _open_hint

    assert "full KMS" in _open_hint(OSError(errno.ENOENT, "No such file or directory"))


def test_a_repeated_open_failure_is_logged_once(monkeypatch, caplog):
    """It retries every 5s for the life of the appliance. Warning on each one buried
    every other line in the log, which is how a five-second heartbeat becomes the only
    thing anyone can see."""
    import logging

    from visiontak_client import cec as cec_module
    from visiontak_client.cec import CecError, CecReader
    from visiontak_client.cec.base import NullCecBackend

    monkeypatch.setattr(cec_module, "RECONNECT_DELAY_SECONDS", 0)
    holder: dict = {}
    attempts = []

    class Refuses(NullCecBackend):
        def open(self):
            attempts.append(1)
            if len(attempts) >= 3:
                holder["reader"]._stop.set()
            raise CecError("cannot open /dev/cec0: [Errno 1] Operation not permitted")

    reader = CecReader(Refuses(), lambda _event: None)
    holder["reader"] = reader
    with caplog.at_level(logging.WARNING, logger="visiontak_client.cec"):
        reader._run()

    assert len(attempts) == 3, "the retry loop did not run"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, f"the same failure was reported {len(warnings)} times"


def test_a_changed_open_failure_is_reported_again(monkeypatch, caplog):
    """Quieting repeats must not quiet a *different* failure — that would hide the
    adapter going from unconnected to genuinely broken."""
    import logging

    from visiontak_client import cec as cec_module
    from visiontak_client.cec import CecError, CecReader
    from visiontak_client.cec.base import NullCecBackend

    monkeypatch.setattr(cec_module, "RECONNECT_DELAY_SECONDS", 0)
    holder: dict = {}
    reasons = ["Operation not permitted", "Operation not permitted", "No such device"]

    class Refuses(NullCecBackend):
        def open(self):
            if not reasons:
                holder["reader"]._stop.set()
                raise CecError("No such device")
            reason = reasons.pop(0)
            if not reasons:
                holder["reader"]._stop.set()
            raise CecError(f"cannot open /dev/cec0: {reason}")

    reader = CecReader(Refuses(), lambda _event: None)
    holder["reader"] = reader
    with caplog.at_level(logging.WARNING, logger="visiontak_client.cec"):
        reader._run()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2, "a changed reason was swallowed as a repeat"


def test_a_failure_to_open_is_reported_not_just_logged(monkeypatch):
    """An unopenable adapter must reach the diagnostics panel. It previously showed
    KernelCecBackend / present for a device that had never opened."""
    from visiontak_client.cec import CecError, CecEventKind, CecReader
    from visiontak_client.cec.base import NullCecBackend

    class Refuses(NullCecBackend):
        def open(self):
            raise CecError("cannot open /dev/cec0: [Errno 1] Operation not permitted")

    seen = []
    holder = {}

    class StopsAfterOneAttempt(Refuses):
        def open(self):
            # Ask the loop to finish, so this exercises one failure and not a retry
            # loop; the wait() after the failure then returns immediately.
            holder["reader"]._stop.set()
            super().open()

    reader = CecReader(StopsAfterOneAttempt(), seen.append)
    holder["reader"] = reader
    reader._run()

    assert [e.kind for e in seen] == [CecEventKind.ADAPTER_LOST]
    assert "Operation not permitted" in seen[0].detail
