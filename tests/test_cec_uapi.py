"""The ioctl numbers encode struct sizes, so a layout mistake silently produces a
wrong request number and an ENOTTY at runtime on real hardware. Pin them here.

These values are only worth as much as their provenance. CEC_DQEVENT was wrong here
and in uapi.py at the same time — both were written from the same mistaken idea of
struct cec_event, so they agreed with each other and not with the kernel, and the test
passed while the read loop failed on every dequeue. Every number below has since been
exercised against a Pi 4 running vc4_hdmi by issuing the ioctl and checking it is not
rejected with ENOTTY. Do not update one of these to match a code change; re-measure.
"""

import ctypes

from visiontak_client.cec import uapi as u


def test_struct_sizes_match_linux_cec_h():
    assert ctypes.sizeof(u.CecMsg) == 56
    assert ctypes.sizeof(u.CecCaps) == 76
    assert ctypes.sizeof(u.CecLogAddrs) == 92
    assert ctypes.sizeof(u.CecEventRaw) == 80


def test_ioctl_numbers_match_the_kernel_headers():
    # Values from `include/uapi/linux/cec.h` on the asm-generic ioctl encoding.
    assert u.CEC_ADAP_G_CAPS == 0xC04C6100
    assert u.CEC_ADAP_G_PHYS_ADDR == 0x80026101
    assert u.CEC_ADAP_S_PHYS_ADDR == 0x40026102
    assert u.CEC_ADAP_G_LOG_ADDRS == 0x805C6103
    assert u.CEC_ADAP_S_LOG_ADDRS == 0xC05C6104
    assert u.CEC_TRANSMIT == 0xC0386105
    assert u.CEC_RECEIVE == 0xC0386106
    assert u.CEC_DQEVENT == 0xC0506107
    assert u.CEC_G_MODE == 0x80046108
    assert u.CEC_S_MODE == 0x40046109


def test_capability_bits_match_linux_cec_h():
    """These were each one bit too high, which made CEC_CAP_LOG_ADDRS test the
    TRANSMIT bit. Every bit the vc4 adapter sets is checked, so a repeat shows up
    here rather than on an adapter with a different capability set."""
    assert u.CEC_CAP_PHYS_ADDR == 1 << 0
    assert u.CEC_CAP_LOG_ADDRS == 1 << 1
    assert u.CEC_CAP_TRANSMIT == 1 << 2
    assert u.CEC_CAP_PASSTHROUGH == 1 << 3
    assert u.CEC_CAP_RC == 1 << 4
    assert u.CEC_CAP_MONITOR_ALL == 1 << 5
    assert u.CEC_CAP_NEEDS_HPD == 1 << 6
    assert u.CEC_CAP_MONITOR_PIN == 1 << 7
    assert u.CEC_CAP_CONNECTOR_INFO == 1 << 8


def test_the_pi_adapter_capabilities_decode():
    """0x11e is what a Pi 4 vc4_hdmi adapter reports. PHYS_ADDR is absent — the
    driver owns the physical address, so the client must not try to set it."""
    caps = 0x0000011E
    assert caps & u.CEC_CAP_LOG_ADDRS
    assert caps & u.CEC_CAP_TRANSMIT
    assert not caps & u.CEC_CAP_PHYS_ADDR


def test_message_header_accessors():
    msg = u.CecMsg()
    payload = bytes([0x40, u.CEC_MSG_USER_CONTROL_PRESSED, 0x01])
    msg.len = len(payload)
    for index, byte in enumerate(payload):
        msg.msg[index] = byte
    assert msg.initiator == 4
    assert msg.destination == 0
    assert msg.opcode == u.CEC_MSG_USER_CONTROL_PRESSED
    assert msg.operands == b"\x01"


def test_poll_message_has_no_opcode():
    msg = u.CecMsg()
    msg.len = 1
    msg.msg[0] = 0x40
    assert msg.opcode is None
    assert msg.operands == b""
