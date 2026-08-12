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
