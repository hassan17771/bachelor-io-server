"""CDC-ACM class driver for the host-mode mock.

:class:`UsbHostCdcDevice` acts as a CDC-ACM device for the target's USB host
middleware. The pipe mechanics and the control-transfer engine come from
:class:`UsbHostCore`; this module adds what is CDC's own: the PSTN request
constants, the descriptor/line-coding personality the mock presents
(:class:`CdcDeviceProfile`), the enumeration bookkeeping
(:class:`EnumerationResult`), and the bulk echo helper.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Union

from peripherals.usb.common.requests import (
    DESC_CONFIGURATION,
    DESC_DEVICE,
    DESC_STRING,
    REQ_TYPE_CLASS,
    REQ_TYPE_MASK,
    REQ_TYPE_STANDARD,
    USB_REQ_GET_DESCRIPTOR,
    USB_REQ_SET_ADDRESS,
    USB_REQ_SET_CONFIGURATION,
)
from peripherals.usb.host.abi import UsbHostSimAbi, UsbHostSimError
from peripherals.usb.host.core import ControlTransferLog, UsbHostCore

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

class UsbHostCdcDevice(UsbHostCore):
    """Acts as a CDC-ACM device for the target's USB host middleware.

    The control-transfer engine and bulk primitives come from
    :class:`UsbHostCore` (and through it every completion goes through
    ``abi.ack_out`` / ``abi.provide_in``, which enforce the required
    ``urb_state = DONE`` last write-ordering rule). This class contributes the
    CDC personality: the descriptors served by :meth:`_respond`, the
    enumeration bookkeeping, and the data-pipe echo helper.
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
        super().__init__(
            abi,
            ctrl_out_ch=ctrl_out_ch,
            ctrl_in_ch=ctrl_in_ch,
            max_data_stage=max_data_stage,
        )
        self.profile = profile if profile is not None else CdcDeviceProfile()
        # CDC data pipes: the host transmits on bulk_out_ch, receives on bulk_in_ch.
        self.bulk_out_ch = bulk_out_ch
        self.bulk_in_ch = bulk_in_ch

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
        if done is not None:
            _done = lambda log: bool(done())  # noqa: E731
        else:
            _done = lambda log: self._cdc_traffic_complete(log)  # noqa: E731

        log = self.service_control_transfers(
            self._respond,
            _done,
            timeout_s=timeout_s,
            setup_timeout_s=setup_timeout_s,
            stage_timeout_s=stage_timeout_s,
        )
        result = self._result_from_log(log)
        if not result.completed:
            raise EnumerationError(
                f"enumeration did not complete within {timeout_s}s ({result.summary()})"
            )
        return result

    @staticmethod
    def _cdc_traffic_complete(log: ControlTransferLog) -> bool:
        """The default completion heuristic: SET_CONFIGURATION and GET_LINE_CODING seen."""
        set_configuration = False
        got_line_coding = False
        for setup in log.setups:
            req_type = setup[0] & REQ_TYPE_MASK
            if req_type == REQ_TYPE_STANDARD and setup[1] == USB_REQ_SET_CONFIGURATION:
                set_configuration = True
            elif req_type == REQ_TYPE_CLASS and setup[1] == CDC_GET_LINE_CODING:
                got_line_coding = True
        return set_configuration and got_line_coding

    @staticmethod
    def _result_from_log(log: ControlTransferLog) -> EnumerationResult:
        """Rebuild the CDC bookkeeping from the generic transfer log."""
        result = EnumerationResult(
            setups=list(log.setups),
            data_out=list(log.data_out),
            truncated=log.truncated,
            completed=log.completed,
        )
        for setup in log.setups:
            req_type = setup[0] & REQ_TYPE_MASK
            b_request = setup[1]
            w_value = setup[2] | (setup[3] << 8)
            if req_type == REQ_TYPE_STANDARD:
                if b_request == USB_REQ_SET_ADDRESS:
                    result.address = w_value & 0xFF
                elif b_request == USB_REQ_SET_CONFIGURATION:
                    result.configuration = w_value & 0xFF
                    result.set_configuration = True
            elif req_type == REQ_TYPE_CLASS and b_request == CDC_GET_LINE_CODING:
                result.got_line_coding = True
        return result

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
        the reply.

        ``response`` is either fixed reply bytes or a callable mapping the
        received payload to the reply -- e.g. ``lambda rx: b"pong " + rx.split()[1]``
        answers ``b"ping 7"`` with ``b"pong 7"``. Returns the :class:`CdcEcho`
        recording what was received and what was sent.
        """
        out_ch = self.bulk_out_ch if bulk_out_ch is None else bulk_out_ch
        in_ch = self.bulk_in_ch if bulk_in_ch is None else bulk_in_ch

        received = self.bulk_out(out_ch, timeout_s=timeout_s)

        reply = response(received) if callable(response) else bytes(response)
        if len(reply) > self.max_data_stage:
            raise ValueError(
                f"CDC reply {len(reply)} bytes exceeds one URB ({self.max_data_stage})"
            )
        self.bulk_in(in_ch, reply, timeout_s=reply_timeout_s)
        return CdcEcho(received=received, sent=reply)
