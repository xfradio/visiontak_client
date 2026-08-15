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
        self._poller: select.poll | None = None
        self._log_addr = u.CEC_LOG_ADDR_UNREGISTERED
        self._phys_addr = u.CEC_PHYS_ADDR_INVALID

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> None:
        try:
            self._fd = os.open(self._device, os.O_RDWR | os.O_NONBLOCK)
        except OSError as exc:
            raise CecError(f"cannot open {self._device}: {exc}") from exc
        self._wake_r, self._wake_w = os.pipe()
        # Registered once rather than per poll(). This is the one loop that runs for
        # the life of the process, so rebuilding the poll object and re-registering
        # both descriptors on every pass was pure churn on the device's weakest CPU.
        self._poller = select.poll()
        self._poller.register(self._fd, select.POLLIN | select.POLLPRI)
        self._poller.register(self._wake_r, select.POLLIN)
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
            self._poller = None
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
            if exc.errno == errno.EBUSY and self._adopt_log_addrs():
                return True
            log.warning("could not claim a logical address: %s", exc)
            return False
        self._log_addr = addrs.log_addr[0]
        log.info("claimed CEC logical address %d (phys 0x%04x)", self._log_addr, self._phys_addr)
        return True

    def _adopt_log_addrs(self) -> bool:
        """Take over a configuration the adapter already carries.

        S_LOG_ADDRS answers EBUSY when the adapter is *already* configured — the
        logical-address configuration belongs to the adapter, not to the file handle
        that set it, so it outlives close(). Our own first claim therefore poisons
        every later one: reopening gets a fresh handle, asks for one logical address,
        and is refused for as long as the adapter stays configured.

        That refusal used to be treated as "not yet, try again", which is right for a
        link that has not settled and wrong here — nothing ever clears it. The client
        then held an open, working adapter while _log_addr stayed UNREGISTERED, and an
        unregistered follower neither transmits nor is sent remote keys. The remote is
        dead, the diagnostics panel says KernelCecBackend, and the log repeats one
        warning every five seconds forever.

        Reconfiguring would mean unconfiguring first (num_log_addrs = 0), which drops
        us off the bus and re-runs address negotiation to arrive back where we already
        are. Reading the existing configuration is enough.
        """
        addrs = u.CecLogAddrs()
        try:
            self._ioctl(u.CEC_ADAP_G_LOG_ADDRS, addrs)
        except OSError as exc:
            log.warning("adapter is busy and its configuration is unreadable: %s", exc)
            return False
        if not addrs.num_log_addrs or addrs.log_addr[0] == u.CEC_LOG_ADDR_UNREGISTERED:
            log.warning("adapter is busy but configured with no usable logical address")
            return False
        self._log_addr = addrs.log_addr[0]
        log.info("adopted CEC logical address %d already configured on %s (phys 0x%04x)",
                 self._log_addr, self._device, self._phys_addr)
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
        self.send_osd_name()

    def send_osd_name(self, destination: int = u.CEC_LOG_ADDR_TV) -> None:
        """Tell the TV what to call this input.

        The kernel already answers <Give OSD Name> from the name in the logical-address
        config, so a TV that asks gets it for free. Not every TV asks: some only label a
        source from an unsolicited <Set OSD Name>, and others cache whatever they saw
        first and never refresh. Sending it on announce covers both without waiting.
        """
        if not self._osd_name:
            return
        self._transmit(destination, u.CEC_MSG_SET_OSD_NAME, self._osd_name)

    # -- receive -----------------------------------------------------------

    def poll(self, timeout: float) -> list[CecEvent]:
        poller = self._poller
        if self._fd is None or poller is None:
            return []
        try:
            ready = poller.poll(timeout * 1000)
        except OSError as exc:
            if exc.errno == errno.EINTR:
                return []
            raise
        if not ready:
            return self._retry_claim()

        events: list[CecEvent] = []
        for fd, flags in ready:
            if fd == self._wake_r:
                return events
            if flags & select.POLLPRI:
                events.extend(self._drain_events())
            if flags & select.POLLIN:
                events.extend(self._drain_messages())
        return events

    def _retry_claim(self) -> list[CecEvent]:
        """Claim a logical address if we still have none.

        open() claims one only when the TV has already supplied a physical address,
        and otherwise waits for a CEC_EVENT_STATE_CHANGE to try again. A link that was
        already up and settled before the daemon started need never produce that event,
        which leaves the adapter open but permanently unregistered — and that state is
        silent in both directions. _transmit() returns early, so <Set OSD Name> is never
        sent and the TV keeps the firmware's "raspberry" label; and an unaddressable
        follower is sent no remote keys, so the whole remote appears dead while the
        diagnostics panel cheerfully reports KernelCecBackend and /dev/cec0 present.

        Runs on poll() timeouts, so at most once every POLL_TIMEOUT_SECONDS and only
        while unregistered.
        """
        if self._log_addr != u.CEC_LOG_ADDR_UNREGISTERED:
            return []
        if self._refresh_phys_addr() == u.CEC_PHYS_ADDR_INVALID:
            return []
        if not self._claim_logical_address():
            return []
        self.announce()
        return [CecEvent(CecEventKind.ADAPTER_READY, detail=f"phys 0x{self._phys_addr:04x}")]

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
