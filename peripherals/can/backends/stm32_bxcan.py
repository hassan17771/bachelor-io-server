"""STM32 bxCAN concrete implementation of :class:`CanSimAbi`.

Translates the portable CAN-sim operations into reads/writes of the
``CanSimBlock`` MMB (see firmware_abi/can/stm32_bxcan.py and Core/Inc/can_sim.h).
Filter matching happens entirely in firmware (HAL_CAN_IRQHandler_Sim replays
the stored filter banks); this backend never reads or writes
``can_sim.filters[]`` -- it only moves frames through the rx slot and the tx[]
mailboxes.
"""
from __future__ import annotations

from typing import Optional

from firmware_abi.can.stm32_bxcan import (
    BLOCK_SIZE,
    CAN_SIM_ID_EXT,
    CAN_SIM_ID_STD,
    CAN_SIM_MAGIC,
    CAN_SIM_VERSION,
    DEFAULT_CAN_SIM_LAYOUT,
    FRAME_SIZE,
    CanSimLayout,
)
from target.gateway import TargetGateway

from peripherals.can.abi import CanFrame, CanSimAbi, CanSimError, CanSimStatus


class Stm32BxcanAbi(CanSimAbi):
    """Drive the STM32 bxCAN MMB over a :class:`TargetGateway`."""

    def __init__(
        self,
        gateway: TargetGateway,
        base_address: int,
        irqn: int,
        layout: CanSimLayout = DEFAULT_CAN_SIM_LAYOUT,
    ) -> None:
        self.gateway = gateway
        self.base_address = base_address
        self.irqn = irqn
        self.layout = layout

    # -- address helpers -----------------------------------------------------

    def _hdr(self, field: str) -> int:
        return self.base_address + getattr(self.layout, field)

    def _tx(self, n: int, field: str) -> int:
        return self.base_address + self.layout.tx(n, field)

    def _rx(self, field: str) -> int:
        return self.base_address + self.layout.rx(field)

    # -- lifecycle -------------------------------------------------------------

    def validate(self) -> CanSimStatus:
        """Read and check the identity header HAL_CAN_Start_Sim publishes.

        The magic is written last by the firmware, so its presence means the
        whole header is valid and the CAN stack is running.
        """
        g = self.gateway
        status = CanSimStatus(
            magic=g.read_u32(self._hdr("magic")),
            version=g.read_u16(self._hdr("version")),
            block_size=g.read_u16(self._hdr("block_size")),
        )
        if status.magic != CAN_SIM_MAGIC:
            raise CanSimError(
                f"bad MMB magic 0x{status.magic:08X} (expected 0x{CAN_SIM_MAGIC:08X}); "
                "is the firmware built with -DCAN_SIMULATION and started?"
            )
        if status.block_size != BLOCK_SIZE:
            raise CanSimError(
                f"MMB block_size {status.block_size} != {BLOCK_SIZE}; ABI mismatch"
            )
        if status.version != CAN_SIM_VERSION:
            raise CanSimError(
                f"MMB version 0x{status.version:04X} != 0x{CAN_SIM_VERSION:04X}"
            )
        return status

    # -- frame exchange --------------------------------------------------------

    def inject_frame(self, frame: CanFrame) -> None:
        """Write ``frame`` into the rx slot, pending last, then pend the IRQ.

        ``pending`` is the flag HAL_CAN_IRQHandler_Sim gates on, so it must be
        the last field written -- the same "ready flag last" rule USB's
        ack_out/provide_in follow for urb_state=DONE.
        """
        max_data = FRAME_SIZE - self.layout.frame.data
        if len(frame.data) > max_data:
            raise ValueError(
                f"CAN payload {len(frame.data)} bytes exceeds {max_data}-byte frame data field"
            )
        g = self.gateway
        g.write_u32(self._rx("id"), frame.id)
        g.write_u8(self._rx("ide"), CAN_SIM_ID_EXT if frame.ide else CAN_SIM_ID_STD)
        g.write_u8(self._rx("rtr"), 1 if frame.rtr else 0)
        g.write_u8(self._rx("dlc"), len(frame.data))
        g.write(self._rx("data"), frame.data)
        g.write_u8(self._rx("pending"), 1)
        g.pend_irq(self.irqn)

    def wait_for_tx(self, timeout_s: float = 3.0) -> CanFrame:
        g = self.gateway
        num_tx = self.layout.num_tx

        def poll() -> Optional[CanFrame]:
            for n in range(num_tx):
                if g.read_u8(self._tx(n, "pending")) == 1:
                    return self._read_tx_frame(n)
            return None

        return g.wait_until(
            poll,
            timeout_s=timeout_s,
            description=f"a pending tx mailbox 0..{num_tx - 1}",
        )

    def _read_tx_frame(self, n: int) -> CanFrame:
        g = self.gateway
        dlc = g.read_u8(self._tx(n, "dlc"))
        data = g.read(self._tx(n, "data"), dlc) if dlc else b""
        return CanFrame(
            id=g.read_u32(self._tx(n, "id")),
            data=data,
            ide=g.read_u8(self._tx(n, "ide")) == CAN_SIM_ID_EXT,
            rtr=g.read_u8(self._tx(n, "rtr")) == 1,
            mailbox=n,
        )

    def ack_tx(self, mailbox: int) -> None:
        self.gateway.write_u8(self._tx(mailbox, "pending"), 0)
