"""The abstract per-MCU contract for a simulated CAN (bxCAN) peripheral.

This module is the portable half of the CAN mock's frame-level mechanics: the
data exchanged with the target (:class:`CanFrame`) and the abstract contract
(:class:`CanSimAbi`)

This file must NOT import from ``backends/``: the ABC layer stays MCU-agnostic
so the same protocol code (``peripherals.can.bus.CanBus``) can drive bxCAN
today and a different CAN IP block tomorrow.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


# --- exchanged data ---------------------------------------------------------

@dataclass(frozen=True)
class CanFrame:
    """One CAN frame, independent of which MMB slot carries it.

    ``ide``/``rtr`` mirror the wire format; extended IDs and remote frames
    are represented but not exercised yet. ``dlc``
    is not a separate field -- it is ``len(data)`` (0-8), so there is nothing
    to keep in sync. ``mailbox`` is only meaningful on a frame returned by
    :meth:`CanSimAbi.wait_for_tx` (which tx slot it came from, for the
    matching :meth:`CanSimAbi.ack_tx` call); -1 on a frame built for
    :meth:`CanSimAbi.inject_frame`, which targets the single rx slot.
    """

    id: int
    data: bytes = b""
    ide: bool = False
    rtr: bool = False
    mailbox: int = -1


@dataclass(frozen=True)
class CanSimStatus:
    """Identity fields the firmware publishes once the CAN stack is up."""

    magic: int
    version: int
    block_size: int


class CanSimError(RuntimeError):
    """Raised when the MMB does not present a valid, running CAN stack."""


# --- abstract per-MCU contract ----------------------------------------------

class CanSimAbi(ABC):
    """The mechanics of one MCU's simulated CAN peripheral.

    A concrete subclass translates these portable operations into reads/writes
    of that MCU's Mocking Memory Block. The protocol layer (:class:`CanBus`)
    is written purely against this interface.
    """

    @abstractmethod
    def validate(self) -> CanSimStatus:
        """Confirm the MMB holds a running CAN stack; raise otherwise."""

    @abstractmethod
    def inject_frame(self, frame: CanFrame) -> None:
        """Bus -> target: write ``frame`` into the rx slot and pend its IRQ."""

    @abstractmethod
    def wait_for_tx(self, timeout_s: float = 3.0) -> CanFrame:
        """Target -> bus: block until a tx mailbox is pending; return it.

        Does not drain the mailbox -- call :meth:`ack_tx` with the returned
        frame's ``mailbox`` once it has been consumed.
        """

    @abstractmethod
    def ack_tx(self, mailbox: int) -> None:
        """Clear a pending tx mailbox once its frame has been consumed."""
