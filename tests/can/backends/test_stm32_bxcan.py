"""Stm32BxcanAbi transport contract.

Verifies every CanSimAbi method -- validate / inject_frame / wait_for_tx /
ack_tx -- against an in-memory MMB (tests.support.fake_gateway), so it runs
with no J-Link and no target attached:

    cd wcet-io-server
    python -m pytest tests/can/backends/test_stm32_bxcan.py

These assert the *Python-side* transport contract -- the exact fields each
call writes, the order it writes them in, and the IRQ it pends. Filter
matching is entirely firmware-side (HAL_CAN_IRQHandler_Sim), so this backend
and this test never touch can_sim.filters[]. The matching firmware behaviour
is covered by can_hal_sim's own host-built unit test and, eventually, the
hardware test under tests/hardware/can/.
"""
from __future__ import annotations

import unittest

from tests.support.fake_gateway import FakeMmbGateway

from firmware_abi.can.stm32_bxcan import (
    BLOCK_SIZE,
    CAN_SIM_ID_EXT,
    CAN_SIM_ID_STD,
    CAN_SIM_MAGIC,
    CAN_SIM_VERSION,
    DEFAULT_CAN_SIM_LAYOUT,
)
from peripherals.can.abi import CanFrame, CanSimError
from peripherals.can.backends.stm32_bxcan import Stm32BxcanAbi
from target.gateway import TargetTimeoutError

MMB_BASE = 0x20002000  # arbitrary -- the fake gateway has no memory map.
TEST_IRQN = 20  # arbitrary -- the real part->irqn table is Task 2.2's concern.


class CanMmbView:
    """Name-based read/write of MMB header/tx/rx fields, for arranging and
    inspecting test state against a layout + base."""

    def __init__(self, gateway: FakeMmbGateway, base: int, layout) -> None:
        self.g = gateway
        self.base = base
        self.layout = layout

    def hdr_addr(self, field: str) -> int:
        return self.base + getattr(self.layout, field)

    def tx_addr(self, n: int, field: str) -> int:
        return self.base + self.layout.tx(n, field)

    def rx_addr(self, field: str) -> int:
        return self.base + self.layout.rx(field)

    def set_u8(self, field: str, value: int) -> None:
        self.g.write_u8(self.hdr_addr(field), value)

    def set_u16(self, field: str, value: int) -> None:
        self.g.write_u16(self.hdr_addr(field), value)

    def set_u32(self, field: str, value: int) -> None:
        self.g.write_u32(self.hdr_addr(field), value)

    def tx_get_u8(self, n: int, field: str) -> int:
        return self.g.read_u8(self.tx_addr(n, field))

    def tx_set_u8(self, n: int, field: str, value: int) -> None:
        self.g.write_u8(self.tx_addr(n, field), value)

    def tx_set_u32(self, n: int, field: str, value: int) -> None:
        self.g.write_u32(self.tx_addr(n, field), value)

    def tx_write(self, n: int, field: str, data: bytes) -> None:
        self.g.write(self.tx_addr(n, field), data)

    def rx_get_u8(self, field: str) -> int:
        return self.g.read_u8(self.rx_addr(field))

    def rx_get_u32(self, field: str) -> int:
        return self.g.read_u32(self.rx_addr(field))

    def rx_read(self, field: str, size: int) -> bytes:
        return self.g.read(self.rx_addr(field), size)

    def put_tx_frame(self, n: int, frame: CanFrame) -> None:
        """Mimic HAL_CAN_AddTxMessage_Sim filling tx[n], pending last."""
        self.tx_set_u32(n, "id", frame.id)
        self.tx_set_u8(n, "ide", CAN_SIM_ID_EXT if frame.ide else CAN_SIM_ID_STD)
        self.tx_set_u8(n, "rtr", 1 if frame.rtr else 0)
        self.tx_set_u8(n, "dlc", len(frame.data))
        self.tx_write(n, "data", frame.data)
        self.tx_set_u8(n, "pending", 1)


class ConstructionTest(unittest.TestCase):
    def test_base_address_and_irqn_are_required(self) -> None:
        # No default for either: base_address depends on the build under
        # test, irqn depends on the part (Key decision 5) -- neither has a
        # sensible universal default.
        with self.assertRaises(TypeError):
            Stm32BxcanAbi(FakeMmbGateway())  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            Stm32BxcanAbi(FakeMmbGateway(), MMB_BASE)  # type: ignore[call-arg]


class ValidateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.layout = DEFAULT_CAN_SIM_LAYOUT
        self.base = MMB_BASE
        self.gw = FakeMmbGateway()
        self.view = CanMmbView(self.gw, self.base, self.layout)
        self.abi = Stm32BxcanAbi(self.gw, base_address=self.base, irqn=TEST_IRQN)
        # Publish a valid identity header (what HAL_CAN_Start_Sim writes).
        self.view.set_u32("magic", CAN_SIM_MAGIC)
        self.view.set_u16("version", CAN_SIM_VERSION)
        self.view.set_u16("block_size", BLOCK_SIZE)
        self.gw.writes.clear()  # ignore the arrange writes in ordering checks

    def test_validate_ok(self) -> None:
        status = self.abi.validate()
        self.assertEqual(status.magic, CAN_SIM_MAGIC)
        self.assertEqual(status.version, CAN_SIM_VERSION)
        self.assertEqual(status.block_size, BLOCK_SIZE)

    def test_validate_rejects_bad_magic(self) -> None:
        self.view.set_u32("magic", 0xDEADBEEF)
        with self.assertRaises(CanSimError):
            self.abi.validate()

    def test_validate_rejects_block_size_mismatch(self) -> None:
        self.view.set_u16("block_size", BLOCK_SIZE + 4)
        with self.assertRaises(CanSimError):
            self.abi.validate()

    def test_validate_rejects_version_mismatch_naming_both(self) -> None:
        self.view.set_u16("version", 0x0002)
        with self.assertRaises(CanSimError) as caught:
            self.abi.validate()
        message = str(caught.exception)
        self.assertIn("0x0002", message)
        self.assertIn(f"0x{CAN_SIM_VERSION:04X}", message)


class FrameExchangeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.layout = DEFAULT_CAN_SIM_LAYOUT
        self.base = MMB_BASE
        self.gw = FakeMmbGateway()
        self.view = CanMmbView(self.gw, self.base, self.layout)
        self.abi = Stm32BxcanAbi(self.gw, base_address=self.base, irqn=TEST_IRQN)

    # -- inject_frame ----------------------------------------------------------

    def test_inject_frame_writes_rx_slot_and_pends_irq(self) -> None:
        self.abi.inject_frame(CanFrame(id=0x123, data=b"\x01\x02\x03"))

        self.assertEqual(self.view.rx_get_u32("id"), 0x123)
        self.assertEqual(self.view.rx_get_u8("ide"), CAN_SIM_ID_STD)
        self.assertEqual(self.view.rx_get_u8("rtr"), 0)
        self.assertEqual(self.view.rx_get_u8("dlc"), 3)
        self.assertEqual(self.view.rx_read("data", 3), b"\x01\x02\x03")
        self.assertEqual(self.view.rx_get_u8("pending"), 1)
        self.assertEqual(self.gw.pended, [TEST_IRQN])

    def test_inject_frame_rejects_oversize_payload(self) -> None:
        with self.assertRaises(ValueError):
            self.abi.inject_frame(CanFrame(id=0x123, data=b"\x00" * 9))

    def test_inject_frame_passes_through_ide_and_rtr(self) -> None:
        self.abi.inject_frame(CanFrame(id=0x456, ide=True, rtr=True))

        self.assertEqual(self.view.rx_get_u8("ide"), CAN_SIM_ID_EXT)
        self.assertEqual(self.view.rx_get_u8("rtr"), 1)

    def test_inject_frame_writes_pending_last_then_pends_irq(self) -> None:
        self.abi.inject_frame(CanFrame(id=0x123, data=b"\xaa"))

        pending_idx = self._first_write_index(self.view.rx_addr("pending"))
        for field in ("id", "ide", "rtr", "dlc", "data"):
            self.assertLess(
                self._first_write_index(self.view.rx_addr(field)),
                pending_idx,
                f"{field} must be written before pending=1",
            )
        # pend_irq happens after every write, not interleaved.
        self.assertEqual(pending_idx, len(self.gw.writes) - 1)

    # -- wait_for_tx / ack_tx ----------------------------------------------------

    def test_wait_for_tx_returns_first_pending_mailbox(self) -> None:
        self.view.put_tx_frame(1, CanFrame(id=0x321, data=b"\xaa\xbb"))

        frame = self.abi.wait_for_tx(timeout_s=0.2)

        self.assertEqual(frame, CanFrame(id=0x321, data=b"\xaa\xbb", mailbox=1))
        # wait_for_tx must not consume the mailbox -- ack_tx does that.
        self.assertEqual(self.view.tx_get_u8(1, "pending"), 1)

    def test_wait_for_tx_reads_ide_and_rtr(self) -> None:
        self.view.put_tx_frame(0, CanFrame(id=0x1FF, ide=True, rtr=True))

        frame = self.abi.wait_for_tx(timeout_s=0.2)

        self.assertTrue(frame.ide)
        self.assertTrue(frame.rtr)

    def test_wait_for_tx_times_out_when_no_mailbox_pending(self) -> None:
        with self.assertRaises(TargetTimeoutError):
            self.abi.wait_for_tx(timeout_s=0.05)

    def test_ack_tx_clears_the_named_mailbox(self) -> None:
        self.view.put_tx_frame(2, CanFrame(id=0x1, data=b"\x00"))

        self.abi.ack_tx(2)

        self.assertEqual(self.view.tx_get_u8(2, "pending"), 0)

    # -- helpers -------------------------------------------------------------

    def _first_write_index(self, address: int) -> int:
        for i, (addr, _) in enumerate(self.gw.writes):
            if addr == address:
                return i
        return -1


if __name__ == "__main__":
    unittest.main()
