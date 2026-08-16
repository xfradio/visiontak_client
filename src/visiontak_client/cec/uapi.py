"""ctypes bindings for the Linux CEC uAPI (`include/uapi/linux/cec.h`).

Structure sizes are computed with `ctypes.sizeof` rather than hardcoded, so the ioctl
numbers stay correct on both armhf and arm64 without a per-arch table.

Only the asm-generic ioctl encoding is supported (x86, arm, arm64, riscv). mips/ppc/sparc
use a different direction encoding and are not targets for this appliance.
"""

from __future__ import annotations

import ctypes

CEC_MAX_MSG_SIZE = 16
CEC_MAX_LOG_ADDRS = 4

_IOC_NONE, _IOC_WRITE, _IOC_READ = 0, 1, 2


def _ioc(direction: int, type_char: str, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(type_char) << 8) | nr


class CecCaps(ctypes.Structure):
    _fields_ = [
        ("driver", ctypes.c_char * 32),
        ("name", ctypes.c_char * 32),
        ("available_log_addrs", ctypes.c_uint32),
        ("capabilities", ctypes.c_uint32),
        ("version", ctypes.c_uint32),
    ]


class CecMsg(ctypes.Structure):
    _fields_ = [
        ("tx_ts", ctypes.c_uint64),
        ("rx_ts", ctypes.c_uint64),
        ("len", ctypes.c_uint32),
        ("timeout", ctypes.c_uint32),
        ("sequence", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("msg", ctypes.c_uint8 * CEC_MAX_MSG_SIZE),
        ("reply", ctypes.c_uint8),
        ("rx_status", ctypes.c_uint8),
        ("tx_status", ctypes.c_uint8),
        ("tx_arb_lost_cnt", ctypes.c_uint8),
        ("tx_nack_cnt", ctypes.c_uint8),
        ("tx_low_drive_cnt", ctypes.c_uint8),
        ("tx_error_cnt", ctypes.c_uint8),
    ]

    @property
    def initiator(self) -> int:
        return (self.msg[0] >> 4) & 0x0F

    @property
    def destination(self) -> int:
        return self.msg[0] & 0x0F

    @property
    def opcode(self) -> int | None:
        return self.msg[1] if self.len >= 2 else None

    @property
    def operands(self) -> bytes:
        return bytes(self.msg[2 : self.len]) if self.len > 2 else b""


class CecLogAddrs(ctypes.Structure):
    _fields_ = [
        ("log_addr", ctypes.c_uint8 * CEC_MAX_LOG_ADDRS),
        ("log_addr_mask", ctypes.c_uint16),
        ("cec_version", ctypes.c_uint8),
        ("num_log_addrs", ctypes.c_uint8),
        ("vendor_id", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("osd_name", ctypes.c_char * 15),
        ("primary_device_type", ctypes.c_uint8 * CEC_MAX_LOG_ADDRS),
        ("log_addr_type", ctypes.c_uint8 * CEC_MAX_LOG_ADDRS),
        ("all_device_types", ctypes.c_uint8 * CEC_MAX_LOG_ADDRS),
        ("features", (ctypes.c_uint8 * 12) * CEC_MAX_LOG_ADDRS),
    ]


class CecEventStateChange(ctypes.Structure):
    _fields_ = [
        ("phys_addr", ctypes.c_uint16),
        ("log_addr_mask", ctypes.c_uint16),
        ("have_conn_info", ctypes.c_uint16),
    ]


class _CecEventUnion(ctypes.Union):
    _fields_ = [
        ("state_change", CecEventStateChange),
        ("lost_msgs", ctypes.c_uint32),
        # 64, not 80. The pad is the same width as v4l2_event.data[64], which is
        # presumably where 80 came from. It made sizeof(cec_event) 96 instead of 80,
        # so CEC_DQEVENT encoded as 0xc0606107 and the driver's switch matched no
        # case at all: every dequeue failed with ENOTTY. That killed the read loop
        # roughly a millisecond after each open, and the reader reopened on a 5s
        # cycle forever. Measured against a running Pi 4 (vc4_hdmi), not transcribed.
        ("raw", ctypes.c_uint8 * 64),
    ]


class CecEventRaw(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("ts", ctypes.c_uint64),
        ("event", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("u", _CecEventUnion),
    ]


CEC_ADAP_G_CAPS = _ioc(_IOC_READ | _IOC_WRITE, "a", 0, ctypes.sizeof(CecCaps))
CEC_ADAP_G_PHYS_ADDR = _ioc(_IOC_READ, "a", 1, ctypes.sizeof(ctypes.c_uint16))
CEC_ADAP_S_PHYS_ADDR = _ioc(_IOC_WRITE, "a", 2, ctypes.sizeof(ctypes.c_uint16))
CEC_ADAP_G_LOG_ADDRS = _ioc(_IOC_READ, "a", 3, ctypes.sizeof(CecLogAddrs))
CEC_ADAP_S_LOG_ADDRS = _ioc(_IOC_READ | _IOC_WRITE, "a", 4, ctypes.sizeof(CecLogAddrs))
CEC_TRANSMIT = _ioc(_IOC_READ | _IOC_WRITE, "a", 5, ctypes.sizeof(CecMsg))
CEC_RECEIVE = _ioc(_IOC_READ | _IOC_WRITE, "a", 6, ctypes.sizeof(CecMsg))
CEC_DQEVENT = _ioc(_IOC_READ | _IOC_WRITE, "a", 7, ctypes.sizeof(CecEventRaw))
CEC_G_MODE = _ioc(_IOC_READ, "a", 8, ctypes.sizeof(ctypes.c_uint32))
CEC_S_MODE = _ioc(_IOC_WRITE, "a", 9, ctypes.sizeof(ctypes.c_uint32))

# Adapter capabilities. These were each one bit too high, so CEC_CAP_LOG_ADDRS read
# the TRANSMIT bit and CEC_CAP_TRANSMIT read PASSTHROUGH. The vc4 adapter happens to
# set all three, so the open() guard passed anyway and the mistake stayed invisible on
# the only hardware it runs on. Pinned by a test now.
CEC_CAP_PHYS_ADDR = 0x00000001
CEC_CAP_LOG_ADDRS = 0x00000002
CEC_CAP_TRANSMIT = 0x00000004
CEC_CAP_PASSTHROUGH = 0x00000008
CEC_CAP_RC = 0x00000010
CEC_CAP_MONITOR_ALL = 0x00000020
CEC_CAP_NEEDS_HPD = 0x00000040
CEC_CAP_MONITOR_PIN = 0x00000080
CEC_CAP_CONNECTOR_INFO = 0x00000100

# Modes
CEC_MODE_INITIATOR = 0x01
CEC_MODE_FOLLOWER = 0x10
CEC_MODE_EXCL_FOLLOWER = 0x20

# Events
CEC_EVENT_STATE_CHANGE = 1
CEC_EVENT_LOST_MSGS = 2
CEC_EVENT_PIN_HPD_LOW = 5
CEC_EVENT_PIN_HPD_HIGH = 6

# Logical addresses / device types
CEC_LOG_ADDR_TV = 0
CEC_LOG_ADDR_UNREGISTERED = 15
CEC_LOG_ADDR_BROADCAST = 15
# What the kernel puts in cec_log_addrs.log_addr[] for a slot that has no address —
# not the same thing as UNREGISTERED, which is a real address a device transmits from.
# It does not fit in the four bits of a message header, so it must never reach one.
CEC_LOG_ADDR_INVALID = 0xFF
CEC_LOG_ADDR_TYPE_PLAYBACK = 4
CEC_OP_PRIM_DEVTYPE_PLAYBACK = 4
CEC_OP_ALL_DEVTYPE_PLAYBACK = 0x08
CEC_OP_CEC_VERSION_1_4 = 0x05
CEC_LOG_ADDRS_FL_ALLOW_UNREG_FALLBACK = 1 << 0

CEC_PHYS_ADDR_INVALID = 0xFFFF

# Opcodes we act on. Core messages (OSD name, vendor id, power status, physical
# address) are answered by the kernel itself while in plain follower mode.
CEC_MSG_ACTIVE_SOURCE = 0x82
CEC_MSG_IMAGE_VIEW_ON = 0x04
CEC_MSG_TEXT_VIEW_ON = 0x0D
CEC_MSG_STANDBY = 0x36
CEC_MSG_SET_STREAM_PATH = 0x86
CEC_MSG_ROUTING_CHANGE = 0x80
CEC_MSG_USER_CONTROL_PRESSED = 0x44
CEC_MSG_USER_CONTROL_RELEASED = 0x45
CEC_MSG_MENU_REQUEST = 0x8D
CEC_MSG_MENU_STATUS = 0x8E
CEC_MSG_GIVE_OSD_NAME = 0x46
CEC_MSG_SET_OSD_NAME = 0x47

CEC_OP_MENU_STATE_ACTIVATED = 0x00
