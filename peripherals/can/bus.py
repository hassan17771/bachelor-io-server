"""CanBus: the whole CAN intent layer, written purely against CanSimAbi.

This one class is both the engine and the "device" the target's bxCAN peripheral
talks to.
"""
from __future__ import annotations

from peripherals.can.abi import CanFrame, CanSimAbi


class CanBus:
    """Puts frames on the simulated bus and drains the ones the target sends.

    ``send_frame``/``receive_frame`` are named from the mock's side, same
    convention as ``UsbHostCdcKeywords``: send puts a frame onto the bus for
    the target to receive; receive is what the target transmitted onto the
    bus.
    """

    def __init__(self, abi: CanSimAbi) -> None:
        self.abi = abi

    def send_frame(
        self, id: int, data: bytes = b"", ide: bool = False, rtr: bool = False
    ) -> None:
        """Bus -> target: inject one frame for the target to receive."""
        self.abi.inject_frame(CanFrame(id=id, data=data, ide=ide, rtr=rtr))

    def receive_frame(self, timeout_s: float = 3.0) -> CanFrame:
        """Target -> bus: block until the target transmits a frame, then ack it."""
        frame = self.abi.wait_for_tx(timeout_s=timeout_s)
        self.abi.ack_tx(frame.mailbox)
        return frame
