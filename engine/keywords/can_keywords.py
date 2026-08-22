"""Keywords for a simulated CAN bus mock."""
from __future__ import annotations

import time

from engine import registry
from peripherals.can.abi import CanFrame, CanSimAbi, CanSimError
from peripherals.can.bus import CanBus

MOCK_NAME = "can_bus"

# HAL_CAN_Start_Sim publishes the block's magic a moment after reset, the same
# gap USB's host stack has. A CAN bus has no attach/detach or enumeration to
# hang the check on, so each keyword confirms the stack is up for itself
# before touching the MMB.
CAN_STACK_TIMEOUT_S = 2.0
CAN_STACK_POLL_S = 0.05


class CanKeywords:
    """Drives the simulated CAN bus the target's bxCAN peripheral talks to.

    `Send CAN Frame`/`Receive CAN Frame` are named from the mock's side, same
    convention as `UsbHostCdcKeywords`: send puts a frame onto the bus for the
    target to receive; receive is what the target transmitted onto the bus.
    """

    def send_can_frame(
        self, id: int, data: bytes = b"", ide: bool = False, rtr: bool = False
    ) -> None:
        """Bus -> target: inject a frame for the target to receive."""
        bus = self._bus()
        self._await_can_stack(bus.abi)
        bus.send_frame(id, data, ide=ide, rtr=rtr)

    def receive_can_frame(self, timeout_s: float = 3.0) -> CanFrame:
        """Target -> bus: block until the target transmits a frame, then ack it."""
        bus = self._bus()
        self._await_can_stack(bus.abi)
        return bus.receive_frame(timeout_s=timeout_s)

    @staticmethod
    def _await_can_stack(abi: CanSimAbi) -> None:
        """Poll `validate()` until it passes; re-raise its last failure on timeout."""
        deadline = time.monotonic() + CAN_STACK_TIMEOUT_S
        while True:
            try:
                abi.validate()
                return
            except CanSimError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(CAN_STACK_POLL_S)

    @staticmethod
    def _bus() -> CanBus:
        return registry.peripheral(MOCK_NAME)
