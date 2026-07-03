"""Class-agnostic USB device engine for the host-mode mock.

:class:`UsbHostCore` services whatever the target's host stack drives at the
mock device -- port bring-up, control transfers on the default pipes, bulk
data -- without knowing any USB class. A class driver (e.g. the CDC one)
subclasses it and supplies the ``responder`` that answers control requests
plus its own class-specific constants and helpers.

This file must NOT import from ``backends/`` or from a class driver: it is
written purely against :class:`UsbHostSimAbi`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from target.gateway import TargetTimeoutError

from peripherals.usb.common.requests import REQ_DIR_DEVICE_TO_HOST, SPEED_FULL
from peripherals.usb.host.abi import UsbHostSimAbi

# Answers a decoded SETUP (the raw 8 bytes) with the data-stage reply bytes.
# Only consulted for device->host requests with a non-zero wLength.
ControlResponder = Callable[[bytes], bytes]


@dataclass
class ControlTransferLog:
    """A class-agnostic record of what :meth:`UsbHostCore.service_control_transfers` serviced."""

    setups: List[bytes] = field(default_factory=list)
    data_out: List[bytes] = field(default_factory=list)
    truncated: bool = False
    completed: bool = False


class UsbHostCore:
    """Acts as a USB device for the target's host middleware, sans class logic.

    Built on an :class:`UsbHostSimAbi`, so it is MCU-agnostic: every transfer
    is completed through ``abi.ack_out`` / ``abi.provide_in``, which enforce
    the required ``urb_state = DONE`` last write-ordering rule.

    ``service_control_transfers`` does not script a fixed list of SETUPs; it
    services whatever control transfers the host stack issues, decoding each
    SETUP packet and answering the data/status stages by direction. That is
    robust to the stack's own ordering (device descriptor in two reads, string
    descriptors, class requests, etc.).
    """

    def __init__(
        self,
        abi: UsbHostSimAbi,
        *,
        ctrl_out_ch: int = 0,
        ctrl_in_ch: int = 1,
        max_data_stage: int = 64,
    ) -> None:
        self.abi = abi
        self.ctrl_out_ch = ctrl_out_ch
        self.ctrl_in_ch = ctrl_in_ch
        # One control data stage is one URB through the firmware in_buf; payloads
        # longer than this cannot be delivered in a single transfer.
        self.max_data_stage = max_data_stage

    # -- bring-up ------------------------------------------------------------

    def attach(self, speed: int = SPEED_FULL, timeout_s: float = 3.0) -> None:
        """Present the device and bring the port up (connect -> reset -> enable).

        Leaves the stack about to enumerate, with the control pipes open.
        """
        self.abi.connect(speed=speed)
        self.abi.wait_for_port_reset(timeout_s=timeout_s)
        self.abi.accept_port_reset(speed=speed)

    # -- control transfers -----------------------------------------------------

    def service_control_transfers(
        self,
        responder: ControlResponder,
        done: Optional[Callable[[ControlTransferLog], bool]] = None,
        *,
        timeout_s: float = 20.0,
        setup_timeout_s: float = 1.0,
        stage_timeout_s: float = 3.0,
    ) -> ControlTransferLog:
        """Service control transfers until ``done`` fires or ``timeout_s`` elapses.

        ``done`` is evaluated on the running log between transfers; when it
        returns True the log comes back with ``completed`` set. With no
        ``done`` (or one that never fires) the loop runs out the clock and
        returns ``completed=False`` -- deciding whether that is an error is
        the caller's business.
        """
        log = ControlTransferLog()
        deadline = time.monotonic() + timeout_s

        while time.monotonic() < deadline:
            if done is not None and done(log):
                log.completed = True
                return log

            remaining = deadline - time.monotonic()
            poll = max(0.05, min(setup_timeout_s, remaining))
            try:
                pkt = self.abi.wait_for_out(self.ctrl_out_ch, timeout_s=poll)
            except TargetTimeoutError:
                continue  # no SETUP yet -- loop re-checks completion / deadline

            if pkt.is_setup:
                self._service_control_transfer(pkt.data, responder, log, stage_timeout_s)
            else:
                # Stray non-SETUP OUT on the control pipe; ack and move on.
                self.abi.ack_out(self.ctrl_out_ch)

        if done is not None and done(log):
            log.completed = True
        return log

    def _service_control_transfer(
        self,
        setup: bytes,
        responder: ControlResponder,
        log: ControlTransferLog,
        stage_timeout_s: float,
    ) -> None:
        """Drive one control transfer's data + status stages off its SETUP.

        The SETUP itself is already pending on the control OUT pipe. Stage
        directions follow the middleware's control FSM:
          * device->host with data -> DATA IN on the control IN pipe, then a
            zero-length STATUS OUT on the control OUT pipe;
          * host->device, no data  -> zero-length STATUS IN on the control IN pipe;
          * host->device with data  -> DATA OUT on the control OUT pipe, then a
            zero-length STATUS IN on the control IN pipe.
        Every completion goes through ack_out / provide_in (DONE written last).
        """
        bm_request_type = setup[0]
        w_length = setup[6] | (setup[7] << 8)
        device_to_host = bool(bm_request_type & REQ_DIR_DEVICE_TO_HOST)

        log.setups.append(bytes(setup))

        # Acknowledge the SETUP stage (DONE last, inside ack_out).
        self.abi.ack_out(self.ctrl_out_ch)

        if w_length == 0:
            if device_to_host:
                self._status_out(stage_timeout_s)
            else:
                # Zero-length STATUS IN acknowledges the request.
                self.abi.provide_in(self.ctrl_in_ch, b"", timeout_s=stage_timeout_s)
            return

        if device_to_host:
            data = responder(setup)[:w_length]
            if len(data) > self.max_data_stage:
                data = data[: self.max_data_stage]
                log.truncated = True
            self.abi.provide_in(self.ctrl_in_ch, data, timeout_s=stage_timeout_s)
            self._status_out(stage_timeout_s)
        else:
            # Host->device data stage (e.g. SET_LINE_CODING): take the payload...
            out_pkt = self.abi.wait_for_out(self.ctrl_out_ch, timeout_s=stage_timeout_s)
            self.abi.ack_out(self.ctrl_out_ch)
            log.data_out.append(bytes(out_pkt.data))
            # ...then acknowledge with a zero-length STATUS IN.
            self.abi.provide_in(self.ctrl_in_ch, b"", timeout_s=stage_timeout_s)

    def _status_out(self, stage_timeout_s: float) -> None:
        """Consume the host's zero-length STATUS OUT on the control OUT pipe."""
        self.abi.wait_for_out(self.ctrl_out_ch, timeout_s=stage_timeout_s)
        self.abi.ack_out(self.ctrl_out_ch)

    # -- bulk data -------------------------------------------------------------

    def bulk_out(self, channel: int, timeout_s: float = 3.0) -> bytes:
        """Receive one bulk-OUT URB (the host's transmit) on ``channel`` and ack it."""
        pkt = self.abi.wait_for_out(channel, timeout_s=timeout_s)
        data = bytes(pkt.data)
        self.abi.ack_out(channel)
        return data

    def bulk_in(self, channel: int, data: bytes, timeout_s: float = 3.0) -> None:
        """Answer the host's pending bulk-IN URB on ``channel`` with ``data``."""
        if len(data) > self.max_data_stage:
            raise ValueError(
                f"bulk payload {len(data)} bytes exceeds one URB ({self.max_data_stage})"
            )
        self.abi.provide_in(channel, data, timeout_s=timeout_s)
