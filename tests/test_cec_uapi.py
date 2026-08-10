"""The ioctl numbers encode struct sizes, so a layout mistake silently produces a
wrong request number and an ENOTTY at runtime on real hardware. Pin them here."""

import ctypes

from visiontak_client.cec import uapi as u


def test_struct_sizes_match_linux_cec_h():
    assert ctypes.sizeof(u.CecMsg) == 56
    assert ctypes.sizeof(u.CecCaps) == 76
    assert ctypes.sizeof(u.CecLogAddrs) == 92
    assert ctypes.sizeof(u.CecEventRaw) == 96


def test_ioctl_numbers_match_the_kernel_headers():
    # Values from `include/uapi/linux/cec.h` on the asm-generic ioctl encoding.
    assert u.CEC_ADAP_G_CAPS == 0xC04C6100
    assert u.CEC_ADAP_G_PHYS_ADDR == 0x80026101
    assert u.CEC_ADAP_S_PHYS_ADDR == 0x40026102
    assert u.CEC_ADAP_G_LOG_ADDRS == 0x805C6103
    assert u.CEC_ADAP_S_LOG_ADDRS == 0xC05C6104
    assert u.CEC_TRANSMIT == 0xC0386105
    assert u.CEC_RECEIVE == 0xC0386106
    assert u.CEC_DQEVENT == 0xC0606107
    assert u.CEC_G_MODE == 0x80046108
    assert u.CEC_S_MODE == 0x40046109


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
