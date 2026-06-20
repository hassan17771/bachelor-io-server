"""MCU-agnostic USB-host peripheral simulation layer.

This module is the portable half of the USB-host mock. It defines:

* the data the IO server exchanges with the target (``UsbOutPacket`` /
  ``UsbChannelInfo``),
* the abstract per-MCU contract (:class:`UsbHostSimAbi`) that a concrete
  backend such as ``Stm32OtgFsHostAbi`` implements, and
* the CDC protocol driver (:class:`UsbHostCdcDevice`) that speaks USB on top of
  that contract -- Phase 4.

Design rule (see CLAUDE.md): this file must NOT import from
``usb_host_otgfs.py``. The ABC layer stays MCU-agnostic so the same protocol
code can drive OTG_FS today and NXP IP3511 / Nordic USBD tomorrow.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable, Union

# --- generic USB constants (standard, not MCU-specific) ---------------------

# Endpoint direction, as encoded in the MMB ep_dir field and bit 7 of bEndpointAddress.
DIR_OUT = 0
DIR_IN = 1

# Transfer types, matching the USB bmAttributes encoding.
EP_TYPE_CTRL = 0
EP_TYPE_ISOC = 1
EP_TYPE_BULK = 2
EP_TYPE_INTR = 3

# Port speed values the MMB carries (mirror USBH_SpeedTypeDef: FS == 1).
SPEED_HIGH = 0
SPEED_FULL = 1
SPEED_LOW = 2

ChannelSelector = Union[int, Iterable[int]]


# --- exchanged data ---------------------------------------------------------

@dataclass(frozen=True)
class UsbOutPacket:
    """A host-to-device transfer the middleware submitted on an OUT/SETUP pipe.

    ``data`` is empty for a zero-length status stage. ``is_setup`` is True for
    the 8-byte SETUP packet that opens a control transfer (the firmware does not
    record the SETUP/DATA token in the MMB, so the concrete ABI infers it from
    the pipe type and length).
    """

    channel: int
    endpoint: int
    is_setup: bool
    data: bytes


@dataclass(frozen=True)
class UsbChannelInfo:
    """The endpoint a pipe (channel) was opened for, read back from the MMB."""

    endpoint: int
    direction: int
    ep_type: int


@dataclass(frozen=True)
class UsbHostSimStatus:
    """Identity fields the firmware publishes once the host stack is up."""

    magic: int
    version: int
    block_size: int


class UsbHostSimError(RuntimeError):
    """Raised when the MMB does not present a valid, running host stack."""


# --- abstract per-MCU contract ----------------------------------------------

class UsbHostSimAbi(ABC):
    """The mechanics of one MCU's simulated USB-host peripheral.

    A concrete subclass translates these portable operations into reads/writes
    of that MCU's Mocking Memory Block. The protocol layer
    (:class:`UsbHostCdcDevice`) is written purely against this interface.
    """

    # -- lifecycle / port ----------------------------------------------------

    @abstractmethod
    def validate(self) -> UsbHostSimStatus:
        """Confirm the MMB holds a running host stack; raise otherwise."""

    @abstractmethod
    def connect(self, speed: int = SPEED_FULL) -> None:
        """Present a device at the given speed and fire the connect event."""

    @abstractmethod
    def disconnect(self) -> None:
        """Remove the device and fire the disconnect event."""

    @abstractmethod
    def wait_for_port_reset(self, timeout_s: float = 3.0) -> None:
        """Block until the stack requests a port reset."""

    @abstractmethod
    def accept_port_reset(self, speed: int = SPEED_FULL) -> None:
        """Clear the reset request, bring the port up, fire the port-enable event."""

    # -- per-transfer servicing ---------------------------------------------

    @abstractmethod
    def wait_for_out(self, channels: ChannelSelector, timeout_s: float = 3.0) -> UsbOutPacket:
        """Block until one of ``channels`` has a pending OUT/SETUP; return it.

        Does not ack the transfer -- call :meth:`ack_out` once the packet has
        been consumed.
        """

    @abstractmethod
    def provide_in(self, channel: int, data: bytes, timeout_s: float = 3.0) -> None:
        """Block until ``channel`` requests an IN, then deliver ``data``."""

    @abstractmethod
    def ack_out(self, channel: int) -> None:
        """Complete a pending OUT/SETUP/status stage on ``channel``."""

    @abstractmethod
    def channel_info(self, channel: int) -> UsbChannelInfo:
        """Report the endpoint a pipe was opened for."""


# --- CDC protocol driver (Phase 4) ------------------------------------------

class UsbHostCdcDevice:
    """Acts as a CDC-ACM device for the target's USB host middleware.

    Implemented in Phase 4 on top of an :class:`UsbHostSimAbi`. Declared here so
    the protocol layer's home is fixed and MCU-agnostic.
    """

    def __init__(self, abi: UsbHostSimAbi) -> None:
        self.abi = abi

    def enumerate(self) -> None:  # pragma: no cover - Phase 4.1
        raise NotImplementedError("UsbHostCdcDevice.enumerate is implemented in task 4.1")

    def wait_and_echo(self, response: bytes) -> bytes:  # pragma: no cover - Phase 4.2
        raise NotImplementedError("UsbHostCdcDevice.wait_and_echo is implemented in task 4.2")
