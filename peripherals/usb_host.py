"""MCU-agnostic USB-host peripheral simulation layer.

This module is the portable half of the USB-host mock. It defines:

* the data the IO server exchanges with the target (``UsbOutPacket`` /
  ``UsbChannelInfo``),
* the abstract per-MCU contract (:class:`UsbHostSimAbi`) that a concrete
  backend such as ``Stm32OtgFsHostAbi`` implements, and
* the CDC protocol driver (:class:`UsbHostCdcDevice`) that speaks USB on top of
  that contract.

This file must NOT import from ``usb_host_otgfs.py``: the ABC layer stays
MCU-agnostic so the same protocol code can drive OTG_FS today and NXP IP3511 /
Nordic USBD tomorrow.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Union

from target.gateway import TargetTimeoutError

# Protocol-generic USB constants live in peripherals.usb.common.requests;
# re-imported here so existing importers keep working until this file's
# contents finish moving into peripherals/usb/host/.
from peripherals.usb.common.requests import (  # noqa: F401
    DESC_CONFIGURATION,
    DESC_DEVICE,
    DESC_STRING,
    DIR_IN,
    DIR_OUT,
    EP_TYPE_BULK,
    EP_TYPE_CTRL,
    EP_TYPE_INTR,
    EP_TYPE_ISOC,
    REQ_DIR_DEVICE_TO_HOST,
    REQ_TYPE_CLASS,
    REQ_TYPE_MASK,
    REQ_TYPE_STANDARD,
    SPEED_FULL,
    SPEED_HIGH,
    SPEED_LOW,
    USB_REQ_GET_DESCRIPTOR,
    USB_REQ_SET_ADDRESS,
    USB_REQ_SET_CONFIGURATION,
)

# The pipe-level ABI contract lives in peripherals.usb.host.abi; re-imported
# here for the same transitional reason.
from peripherals.usb.host.abi import (  # noqa: F401
    ChannelSelector,
    UsbChannelInfo,
    UsbHostSimAbi,
    UsbHostSimError,
    UsbHostSimStatus,
    UsbOutPacket,
)


# --- CDC control-request encoding --------------------------------------------

# CDC PSTN class-specific requests (CDC120 / PSTN120 sec 6.3).
CDC_GET_LINE_CODING = 0x21
CDC_SET_LINE_CODING = 0x20
CDC_SET_CONTROL_LINE_STATE = 0x22

# Default CDC data-pipe assignment: the host transmits on the bulk-OUT pipe
# (EP 0x01) and receives on the bulk-IN pipe (EP 0x81); USBH_AllocPipe hands
# these out as pipes 3 and 4 during class init.
CDC_BULK_OUT_CH = 3
CDC_BULK_IN_CH = 4

# Default CDC-ACM device personality the mock presents during enumeration,
# mirroring the STM32 Virtual COM Port (VID 0x0483 / PID 0x5740).
#
# The configuration descriptor is a compact 62-byte CDC-ACM config (it drops the
# optional Call Management functional descriptor) so the whole config fits one
# 64-byte URB: a control data stage moves through the 64-byte MMB in_buf in a
# single URB, and the host re-requests the config using the wTotalLength it reads
# here. USBH_CDC_InterfaceInit binds off the interface/endpoint descriptors and
# ignores the functional ones, so the trimmed config is fully accepted.
_DEFAULT_DEVICE_DESCRIPTOR = bytes(
    [
        0x12, 0x01, 0x00, 0x02, 0x02, 0x00, 0x00, 0x40,
        0x83, 0x04, 0x40, 0x57, 0x00, 0x02, 0x01, 0x02, 0x03, 0x01,
    ]
)

_DEFAULT_CONFIG_DESCRIPTOR = bytes(
    [
        # Configuration descriptor (9), wTotalLength = 62 (0x003E)
        0x09, 0x02, 0x3E, 0x00, 0x02, 0x01, 0x00, 0xC0, 0x32,
        # Interface 0 -- CDC Communication, 1 endpoint (9)
        0x09, 0x04, 0x00, 0x00, 0x01, 0x02, 0x02, 0x01, 0x00,
        # CDC Header functional descriptor (5)
        0x05, 0x24, 0x00, 0x10, 0x01,
        # CDC Abstract Control Management functional descriptor (4)
        0x04, 0x24, 0x02, 0x02,
        # CDC Union functional descriptor (5)
        0x05, 0x24, 0x06, 0x00, 0x01,
        # Notification endpoint 0x82, interrupt IN (7)
        0x07, 0x05, 0x82, 0x03, 0x08, 0x00, 0x10,
        # Interface 1 -- CDC Data, 2 endpoints (9)
        0x09, 0x04, 0x01, 0x00, 0x02, 0x0A, 0x00, 0x00, 0x00,
        # Bulk OUT endpoint 0x01 (7)
        0x07, 0x05, 0x01, 0x02, 0x40, 0x00, 0x00,
        # Bulk IN endpoint 0x81 (7)
        0x07, 0x05, 0x81, 0x02, 0x40, 0x00, 0x00,
    ]
)

# 115200 baud, 1 stop bit, no parity, 8 data bits.
_DEFAULT_LINE_CODING = bytes([0x00, 0xC2, 0x01, 0x00, 0x00, 0x00, 0x08])

# iManufacturer / iProduct / iSerialNumber indices in the device descriptor.
_DEFAULT_STRINGS: Dict[int, str] = {
    1: "STMicroelectronics",
    2: "STM32 Virtual ComPort",
    3: "000000000123",
}


@dataclass(frozen=True)
class CdcDeviceProfile:
    """The descriptors and line coding the mock CDC device replies with.

    Each value the host can pull in one control transfer must fit a single URB
    (see ``UsbHostCdcDevice.max_data_stage``); the defaults are sized for that.
    """

    device_descriptor: bytes = _DEFAULT_DEVICE_DESCRIPTOR
    config_descriptor: bytes = _DEFAULT_CONFIG_DESCRIPTOR
    line_coding: bytes = _DEFAULT_LINE_CODING
    strings: Mapping[int, str] = field(default_factory=lambda: dict(_DEFAULT_STRINGS))
    lang_id: int = 0x0409  # US English


@dataclass
class EnumerationResult:
    """A record of what :meth:`UsbHostCdcDevice.enumerate` serviced."""

    setups: List[bytes] = field(default_factory=list)
    data_out: List[bytes] = field(default_factory=list)
    address: Optional[int] = None
    configuration: Optional[int] = None
    set_configuration: bool = False
    got_line_coding: bool = False
    truncated: bool = False
    completed: bool = False

    def summary(self) -> str:
        return (
            f"setups={len(self.setups)} address={self.address} "
            f"configuration={self.configuration} got_line_coding={self.got_line_coding} "
            f"truncated={self.truncated} completed={self.completed}"
        )


class EnumerationError(UsbHostSimError):
    """Raised when enumeration does not complete within the deadline."""


@dataclass(frozen=True)
class CdcEcho:
    """One CDC bulk round-trip serviced by :meth:`UsbHostCdcDevice.wait_and_echo`.

    ``received`` is the payload the host transmitted (its ``USBH_CDC_Transmit``);
    ``sent`` is the reply delivered back to the host (its ``USBH_CDC_Receive``),
    so the target's RX buffer ends up holding ``sent``.
    """

    received: bytes
    sent: bytes


# --- CDC protocol driver -----------------------------------------------------

class UsbHostCdcDevice:
    """Acts as a CDC-ACM device for the target's USB host middleware.

    Built on an :class:`UsbHostSimAbi`, so it is MCU-agnostic: every transfer is
    completed through ``abi.ack_out`` / ``abi.provide_in``, which enforce the
    required ``urb_state = DONE`` last write-ordering rule.

    ``enumerate`` does not script a fixed list of SETUPs; it services whatever
    control transfers the host stack issues, decoding each SETUP packet and
    answering the data/status stages by direction. That is robust to the stack's
    own ordering (device descriptor in two reads, string descriptors, the CDC
    class GET_LINE_CODING, etc.).
    """

    def __init__(
        self,
        abi: UsbHostSimAbi,
        profile: Optional[CdcDeviceProfile] = None,
        *,
        ctrl_out_ch: int = 0,
        ctrl_in_ch: int = 1,
        bulk_out_ch: int = CDC_BULK_OUT_CH,
        bulk_in_ch: int = CDC_BULK_IN_CH,
        max_data_stage: int = 64,
    ) -> None:
        self.abi = abi
        self.profile = profile if profile is not None else CdcDeviceProfile()
        self.ctrl_out_ch = ctrl_out_ch
        self.ctrl_in_ch = ctrl_in_ch
        # CDC data pipes: the host transmits on bulk_out_ch, receives on bulk_in_ch.
        self.bulk_out_ch = bulk_out_ch
        self.bulk_in_ch = bulk_in_ch
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

    # -- enumeration ---------------------------------------------------------

    def enumerate(
        self,
        done: Optional[Callable[[], bool]] = None,
        *,
        timeout_s: float = 20.0,
        setup_timeout_s: float = 1.0,
        stage_timeout_s: float = 3.0,
    ) -> EnumerationResult:
        """Service control transfers until the device is configured and active.

        ``done`` is an optional completion predicate evaluated between transfers
        -- the hardware test passes ``lambda: Appli_state == APPLICATION_READY``,
        the firmware-side signal that the CDC class went active. With no ``done``
        the driver stops once it has serviced SET_CONFIGURATION and the CDC
        GET_LINE_CODING (the same traffic that triggers APPLICATION_READY).

        Raises :class:`EnumerationError` if neither condition is met before
        ``timeout_s`` elapses.
        """
        result = EnumerationResult()
        deadline = time.monotonic() + timeout_s

        while time.monotonic() < deadline:
            if self._is_complete(done, result):
                result.completed = True
                return result

            remaining = deadline - time.monotonic()
            poll = max(0.05, min(setup_timeout_s, remaining))
            try:
                pkt = self.abi.wait_for_out(self.ctrl_out_ch, timeout_s=poll)
            except TargetTimeoutError:
                continue  # no SETUP yet -- loop re-checks completion / deadline

            if pkt.is_setup:
                self._service_control_transfer(pkt.data, result, stage_timeout_s)
            else:
                # Stray non-SETUP OUT on the control pipe; ack and move on.
                self.abi.ack_out(self.ctrl_out_ch)

        if self._is_complete(done, result):
            result.completed = True
            return result
        raise EnumerationError(
            f"enumeration did not complete within {timeout_s}s ({result.summary()})"
        )

    def _is_complete(self, done: Optional[Callable[[], bool]], result: EnumerationResult) -> bool:
        if done is not None:
            return bool(done())
        return result.set_configuration and result.got_line_coding

    def _service_control_transfer(
        self, setup: bytes, result: EnumerationResult, stage_timeout_s: float
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
        b_request = setup[1]
        w_value = setup[2] | (setup[3] << 8)
        w_length = setup[6] | (setup[7] << 8)
        device_to_host = bool(bm_request_type & REQ_DIR_DEVICE_TO_HOST)

        result.setups.append(bytes(setup))
        self._note_request(result, bm_request_type, b_request, w_value)

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
            data = self._respond(setup)[:w_length]
            if len(data) > self.max_data_stage:
                data = data[: self.max_data_stage]
                result.truncated = True
            self.abi.provide_in(self.ctrl_in_ch, data, timeout_s=stage_timeout_s)
            self._status_out(stage_timeout_s)
        else:
            # Host->device data stage (e.g. SET_LINE_CODING): take the payload...
            out_pkt = self.abi.wait_for_out(self.ctrl_out_ch, timeout_s=stage_timeout_s)
            self.abi.ack_out(self.ctrl_out_ch)
            result.data_out.append(bytes(out_pkt.data))
            # ...then acknowledge with a zero-length STATUS IN.
            self.abi.provide_in(self.ctrl_in_ch, b"", timeout_s=stage_timeout_s)

    def _status_out(self, stage_timeout_s: float) -> None:
        """Consume the host's zero-length STATUS OUT on the control OUT pipe."""
        self.abi.wait_for_out(self.ctrl_out_ch, timeout_s=stage_timeout_s)
        self.abi.ack_out(self.ctrl_out_ch)

    def _note_request(
        self, result: EnumerationResult, bm_request_type: int, b_request: int, w_value: int
    ) -> None:
        req_type = bm_request_type & REQ_TYPE_MASK
        if req_type == REQ_TYPE_STANDARD:
            if b_request == USB_REQ_SET_ADDRESS:
                result.address = w_value & 0xFF
            elif b_request == USB_REQ_SET_CONFIGURATION:
                result.configuration = w_value & 0xFF
                result.set_configuration = True
        elif req_type == REQ_TYPE_CLASS and b_request == CDC_GET_LINE_CODING:
            result.got_line_coding = True

    def _respond(self, setup: bytes) -> bytes:
        """The data bytes for a device->host control request (pre-truncation)."""
        bm_request_type = setup[0]
        b_request = setup[1]
        w_value = setup[2] | (setup[3] << 8)
        req_type = bm_request_type & REQ_TYPE_MASK

        if req_type == REQ_TYPE_STANDARD and b_request == USB_REQ_GET_DESCRIPTOR:
            desc_type = (w_value >> 8) & 0xFF
            desc_index = w_value & 0xFF
            if desc_type == DESC_DEVICE:
                return self.profile.device_descriptor
            if desc_type == DESC_CONFIGURATION:
                return self.profile.config_descriptor
            if desc_type == DESC_STRING:
                return self._string_descriptor(desc_index)
            return b""
        if req_type == REQ_TYPE_CLASS and b_request == CDC_GET_LINE_CODING:
            return self.profile.line_coding
        return b""

    def _string_descriptor(self, index: int) -> bytes:
        """Build a USB string descriptor (index 0 is the LANGID array)."""
        if index == 0:
            return bytes([4, DESC_STRING, self.profile.lang_id & 0xFF,
                          (self.profile.lang_id >> 8) & 0xFF])
        text = self.profile.strings.get(index, "?")
        encoded = text.encode("utf-16-le")
        return bytes([len(encoded) + 2, DESC_STRING]) + encoded

    # -- CDC bulk data -------------------------------------------------------

    def wait_and_echo(
        self,
        response: Union[bytes, Callable[[bytes], bytes]] = b"hello\r\n",
        *,
        bulk_out_ch: Optional[int] = None,
        bulk_in_ch: Optional[int] = None,
        timeout_s: float = 5.0,
        reply_timeout_s: float = 3.0,
    ) -> CdcEcho:
        """Service one CDC bulk round-trip: read the host's transmit, then reply.

        The target's application calls ``USBH_CDC_Transmit`` (a single bulk-OUT
        URB on ``bulk_out_ch``) and then ``USBH_CDC_Receive`` (a bulk-IN URB on
        ``bulk_in_ch``). This blocks for the OUT, acks it, computes the reply,
        and delivers it on the IN pipe -- so the host's RX buffer ends up holding
        the reply. Both completions go through ``abi.ack_out`` / ``abi.provide_in``,
        which keep the required ``urb_state = DONE`` last write-ordering.

        ``response`` is either fixed reply bytes or a callable mapping the
        received payload to the reply -- e.g. ``lambda rx: b"pong " + rx.split()[1]``
        answers ``b"ping 7"`` with ``b"pong 7"``. Returns the :class:`CdcEcho`
        recording what was received and what was sent.
        """
        out_ch = self.bulk_out_ch if bulk_out_ch is None else bulk_out_ch
        in_ch = self.bulk_in_ch if bulk_in_ch is None else bulk_in_ch

        out_pkt = self.abi.wait_for_out(out_ch, timeout_s=timeout_s)
        received = bytes(out_pkt.data)
        self.abi.ack_out(out_ch)

        reply = response(received) if callable(response) else bytes(response)
        if len(reply) > self.max_data_stage:
            raise ValueError(
                f"CDC reply {len(reply)} bytes exceeds one URB ({self.max_data_stage})"
            )
        self.abi.provide_in(in_ch, reply, timeout_s=reply_timeout_s)
        return CdcEcho(received=received, sent=reply)
