"""Kernel CEC backend — talks to /dev/cecN directly.

On Raspberry Pi 4/5 under Ubuntu Core the vc4 DRM driver exposes the HDMI CEC adapter
as /dev/cec0, so no userspace CEC library is needed on the image.

We register as a *playback* device in plain follower mode. Plain follower means the
kernel still answers the mandatory core messages (physical address, OSD name, vendor
id, power status) on our behalf; we only deal with remote key presses and routing.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import logging
import os
import select
import threading

from . import uapi as u
from .base import CecBackend, CecError, CecEvent, CecEventKind

log = logging.getLogger(__name__)

VENDOR_ID_UNKNOWN = 0x000000


class KernelCecBackend(CecBackend):
    def __init__(self, device: str = "/dev/cec0", osd_name: str = "VisionTAK") -> None:
        self._device = device
        self._osd_name = osd_name.encode()[:14]
        self._fd: int | None = None
        self._wake_r: int | None = None
        self._wake_w: int | None = None
        self._lock = threading.Lock()
        self._log_addr = u.CEC_LOG_ADDR_UNREGISTERED
        self._phys_addr = u.CEC_PHYS_ADDR_INVALID

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> None:
        try:
            self._fd = os.open(self._device, os.O_RDWR | os.O_NONBLOCK)
        except OSError as exc:
            raise CecError(f"cannot open {self._device}: {exc}") from exc
        self._wake_r, self._wake_w = os.pipe()
        caps = self._caps()
        log.info("cec adapter %s: driver=%s name=%s caps=0x%08x",
                 self._device, caps.driver.decode(), caps.name.decode(), caps.capabilities)
        if not caps.capabilities & u.CEC_CAP_LOG_ADDRS:
            raise CecError(f"{self._device} cannot claim logical addresses")
        self._set_mode(u.CEC_MODE_INITIATOR | u.CEC_MODE_FOLLOWER)
        self._refresh_phys_addr()
        self._claim_logical_address()

    def close(self) -> None:
        with self._lock:
            if self._wake_w is not None:
                os.write(self._wake_w, b"x")
            fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)
        for pipe_fd in (self._wake_r, self._wake_w):
            if pipe_fd is not None:
                os.close(pipe_fd)
        self._wake_r = self._wake_w = None

    # -- adapter setup -----------------------------------------------------

    def _ioctl(self, request: int, payload):  # noqa: ANN001 - ctypes payload
        if self._fd is None:
            raise CecError("adapter is closed")
        fcntl.ioctl(self._fd, request, payload)
        return payload

    def _caps(self) -> u.CecCaps:
        return self._ioctl(u.CEC_ADAP_G_CAPS, u.CecCaps())

    def _set_mode(self, mode: int) -> None:
        self._ioctl(u.CEC_S_MODE, ctypes.c_uint32(mode))

    def _refresh_phys_addr(self) -> int:
        value = self._ioctl(u.CEC_ADAP_G_PHYS_ADDR, ctypes.c_uint16())
        self._phys_addr = value.value
        return self._phys_addr

    def _claim_logical_address(self) -> bool:
        """Claim a playback logical address. No-op until the TV gives us an EDID."""
        if self._phys_addr == u.CEC_PHYS_ADDR_INVALID:
            log.info("no physical address yet (HDMI not ready); deferring logical address")
            return False
        addrs = u.CecLogAddrs()
        addrs.cec_version = u.CEC_OP_CEC_VERSION_1_4
        addrs.num_log_addrs = 1
        addrs.vendor_id = VENDOR_ID_UNKNOWN
        addrs.flags = u.CEC_LOG_ADDRS_FL_ALLOW_UNREG_FALLBACK
        addrs.osd_name = self._osd_name
        addrs.primary_device_type[0] = u.CEC_OP_PRIM_DEVTYPE_PLAYBACK
        addrs.log_addr_type[0] = u.CEC_LOG_ADDR_TYPE_PLAYBACK
        addrs.all_device_types[0] = u.CEC_OP_ALL_DEVTYPE_PLAYBACK
        try:
            self._ioctl(u.CEC_ADAP_S_LOG_ADDRS, addrs)
        except OSError as exc:
            log.warning("could not claim a logical address: %s", exc)
            return False
        self._log_addr = addrs.log_addr[0]
        log.info("claimed CEC logical address %d (phys 0x%04x)", self._log_addr, self._phys_addr)
        return True

    # -- transmit ----------------------------------------------------------

    def _transmit(self, destination: int, opcode: int, operands: bytes = b"") -> None:
        if self._log_addr == u.CEC_LOG_ADDR_UNREGISTERED:
            log.debug("not transmitting 0x%02x: no logical address", opcode)
            return
        msg = u.CecMsg()
        payload = bytes([(self._log_addr << 4) | destination, opcode]) + operands
        msg.len = len(payload)
        for index, byte in enumerate(payload):
            msg.msg[index] = byte
        try:
            self._ioctl(u.CEC_TRANSMIT, msg)
        except OSError as exc:
            log.warning("cec transmit 0x%02x failed: %s", opcode, exc)

    def announce(self) -> None:
        """Power the TV on and claim the input. Safe to call repeatedly."""
        if self._phys_addr == u.CEC_PHYS_ADDR_INVALID:
            return
        self._transmit(u.CEC_LOG_ADDR_TV, u.CEC_MSG_IMAGE_VIEW_ON)
        self._transmit(
            u.CEC_LOG_ADDR_BROADCAST,
            u.CEC_MSG_ACTIVE_SOURCE,
            bytes([self._phys_addr >> 8, self._phys_addr & 0xFF]),
        )

    # -- receive -----------------------------------------------------------

    def poll(self, timeout: float) -> list[CecEvent]:
        if self._fd is None:
            return []
        poller = select.poll()
        poller.register(self._fd, select.POLLIN | select.POLLPRI)
        if self._wake_r is not None:
            poller.register(self._wake_r, select.POLLIN)
        try:
            ready = poller.poll(timeout * 1000)
        except OSError as exc:
            if exc.errno == errno.EINTR:
                return []
            raise
        if not ready:
            return []

        events: list[CecEvent] = []
        for fd, flags in ready:
            if fd == self._wake_r:
                return events
            if flags & select.POLLPRI:
                events.extend(self._drain_events())
            if flags & select.POLLIN:
                events.extend(self._drain_messages())
        return events

    def _drain_events(self) -> list[CecEvent]:
        out: list[CecEvent] = []
        while True:
            raw = u.CecEventRaw()
            try:
                self._ioctl(u.CEC_DQEVENT, raw)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    return out
                raise
            if raw.event == u.CEC_EVENT_STATE_CHANGE:
                out.extend(self._on_state_change(raw.state_change.phys_addr))
            elif raw.event == u.CEC_EVENT_LOST_MSGS:
                log.warning("cec messages were dropped: receive loop is too slow")

    def _on_state_change(self, phys_addr: int) -> list[CecEvent]:
        previous, self._phys_addr = self._phys_addr, phys_addr
        if phys_addr == u.CEC_PHYS_ADDR_INVALID:
            self._log_addr = u.CEC_LOG_ADDR_UNREGISTERED
            log.info("HDMI link lost")
            return [CecEvent(CecEventKind.ADAPTER_LOST, detail="hdmi unplugged")]
        if previous != phys_addr:
            log.info("HDMI physical address is now 0x%04x", phys_addr)
            if self._claim_logical_address():
                self.announce()
                return [CecEvent(CecEventKind.ADAPTER_READY, detail=f"phys 0x{phys_addr:04x}")]
        return []

    def _drain_messages(self) -> list[CecEvent]:
        out: list[CecEvent] = []
        while True:
            msg = u.CecMsg()
            msg.timeout = 0
            try:
                self._ioctl(u.CEC_RECEIVE, msg)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.ETIMEDOUT):
                    return out
                raise
            event = self._decode(msg)
            if event is not None:
                out.append(event)

    def _decode(self, msg: u.CecMsg) -> CecEvent | None:
        opcode, operands = msg.opcode, msg.operands
        if opcode == u.CEC_MSG_USER_CONTROL_PRESSED and operands:
            return CecEvent(CecEventKind.KEY_PRESS, key_code=operands[0])
        if opcode == u.CEC_MSG_USER_CONTROL_RELEASED:
            return CecEvent(CecEventKind.KEY_RELEASE)
        if opcode == u.CEC_MSG_STANDBY:
            return CecEvent(CecEventKind.STANDBY, detail="tv standby")
        if opcode in (u.CEC_MSG_IMAGE_VIEW_ON, u.CEC_MSG_TEXT_VIEW_ON):
            return CecEvent(CecEventKind.WAKE, detail="tv view on")
        if opcode == u.CEC_MSG_SET_STREAM_PATH and len(operands) >= 2:
            requested = (operands[0] << 8) | operands[1]
            if requested == self._phys_addr:
                self.announce()
                return CecEvent(CecEventKind.WAKE, detail="stream path")
            return CecEvent(CecEventKind.ACTIVE_SOURCE_LOST, detail=f"path 0x{requested:04x}")
        if opcode == u.CEC_MSG_ACTIVE_SOURCE and len(operands) >= 2:
            requested = (operands[0] << 8) | operands[1]
            if requested != self._phys_addr:
                return CecEvent(CecEventKind.ACTIVE_SOURCE_LOST, detail=f"source 0x{requested:04x}")
        return None
