"""Stm32OtgFsHostAbi transport contract.

Verifies every UsbHostSimAbi method -- lifecycle/port (validate / connect /
disconnect / wait_for_port_reset / accept_port_reset) and per-transfer
servicing (wait_for_out / provide_in / ack_out / channel_info) -- against an
in-memory MMB (tests.support.fake_gateway), so it runs with no J-Link and no
target attached:

    cd wcet-io-server
    python -m pytest tests/usb/host/backends/test_stm32_otgfs.py

These assert the *Python-side* transport contract -- the exact fields each call
writes, the order it writes them in, and the events it pends. The matching
firmware behaviour is covered by the hardware tests under
tests/hardware/usb/host/.

Covers the first SETUP the IO server must read back (80 06 00 01 00 00 08 00)
plus the critical write-ordering rule: ack_out and provide_in must write
urb_state=DONE LAST.
"""
from __future__ import annotations

import unittest

from tests.support.fake_gateway import FakeMmbGateway, MmbView

from firmware_abi.usb.host.stm32_otgfs import (
    BLOCK_SIZE,
    BUF_SIZE,
    CDC_DEVICE_DESCRIPTOR,
    DEFAULT_USBH_SIM_LAYOUT,
    EP_DIR_IN,
    EP_DIR_OUT,
    EP_TYPE_BULK,
    EP_TYPE_CTRL,
    OTG_FS_IRQn,
    URB_DONE,
    URB_IDLE,
    USBH_SIM_BASE,
    USBH_SIM_MAGIC,
    USBH_SIM_VERSION,
)
from peripherals.usb.common.requests import SPEED_FULL
from peripherals.usb.host.abi import UsbHostSimError, UsbOutPacket
from peripherals.usb.host.backends.stm32_otgfs import Stm32OtgFsHostAbi
from target.gateway import TargetTimeoutError

# The first SETUP CDC enumeration emits: GET_DESCRIPTOR(Device, wLength=8).
GET_DESCRIPTOR_DEVICE_8 = bytes([0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x08, 0x00])

# Deterministic pipe allocation: ch0 EP0-OUT, ch1 EP0-IN,
# ch3 bulk-OUT EP0x01, ch4 bulk-IN EP0x81.
CTRL_OUT_CH = 0
CTRL_IN_CH = 1
BULK_OUT_CH = 3
BULK_IN_CH = 4


class LifecyclePortTest(unittest.TestCase):
    """validate / connect / disconnect / wait_for_port_reset / accept_port_reset."""

    def setUp(self) -> None:
        self.layout = DEFAULT_USBH_SIM_LAYOUT
        self.base = USBH_SIM_BASE
        self.gw = FakeMmbGateway()
        self.view = MmbView(self.gw, self.base, self.layout)
        self.abi = Stm32OtgFsHostAbi(self.gw, base_address=self.base, layout=self.layout)
        # Publish a valid identity header (what USBH_LL_Start writes).
        self.view.set_u32("magic", USBH_SIM_MAGIC)
        self.view.set_u16("version", USBH_SIM_VERSION)
        self.view.set_u16("block_size", BLOCK_SIZE)
        self.gw.writes.clear()  # ignore the arrange writes in ordering checks

    # -- validate ------------------------------------------------------------

    def test_validate_ok(self) -> None:
        status = self.abi.validate()
        self.assertEqual(status.magic, USBH_SIM_MAGIC)
        self.assertEqual(status.version, USBH_SIM_VERSION)
        self.assertEqual(status.block_size, BLOCK_SIZE)

    def test_validate_rejects_bad_magic(self) -> None:
        self.view.set_u32("magic", 0xDEADBEEF)
        with self.assertRaises(UsbHostSimError):
            self.abi.validate()

    def test_validate_rejects_block_size_mismatch(self) -> None:
        self.view.set_u16("block_size", BLOCK_SIZE + 4)
        with self.assertRaises(UsbHostSimError):
            self.abi.validate()

    # -- connect / disconnect ------------------------------------------------

    def test_connect_writes_flags_and_pends_irq(self) -> None:
        self.abi.connect()
        self.assertEqual(self.view.get_u8("connected"), 1)
        self.assertEqual(self.view.get_u8("ev_connect"), 1)
        self.assertEqual(self.view.get_u8("port_speed"), SPEED_FULL)
        self.assertEqual(self.gw.pended, [OTG_FS_IRQn])

    def test_disconnect_writes_flags_and_pends_irq(self) -> None:
        self.abi.disconnect()
        self.assertEqual(self.view.get_u8("connected"), 0)
        self.assertEqual(self.view.get_u8("ev_disconnect"), 1)
        self.assertEqual(self.gw.pended, [OTG_FS_IRQn])

    # -- wait_for_port_reset -------------------------------------------------

    def test_wait_for_port_reset_returns_when_set(self) -> None:
        self.view.set_u8("reset_req", 1)
        self.abi.wait_for_port_reset(timeout_s=0.2)  # must not raise

    def test_wait_for_port_reset_times_out(self) -> None:
        self.view.set_u8("reset_req", 0)
        with self.assertRaises(TargetTimeoutError):
            self.abi.wait_for_port_reset(timeout_s=0.05)

    # -- accept_port_reset ---------------------------------------------------

    def test_accept_port_reset_writes_flags_and_pends_irq(self) -> None:
        self.view.set_u8("reset_req", 1)
        self.abi.accept_port_reset()
        self.assertEqual(self.view.get_u8("reset_req"), 0)
        self.assertEqual(self.view.get_u8("port_enabled"), 1)
        self.assertEqual(self.view.get_u8("ev_port_en"), 1)
        self.assertEqual(self.view.get_u8("port_speed"), SPEED_FULL)
        self.assertEqual(self.gw.pended, [OTG_FS_IRQn])

    # -- full connect -> reset -> accept sequence ----------------------------

    def test_full_attach_sequence(self) -> None:
        """connect() then the target (emulated) requests a reset; we wait for
        it and accept it, leaving the port enabled."""

        def firmware_irq(gw: FakeMmbGateway, irqn: int) -> None:
            # Model the OTG_FS ISR: on a connect event the stack attaches and
            # calls USBH_LL_ResetPort, which raises reset_req.
            if self.view.get_u8("ev_connect") == 1:
                self.view.set_u8("ev_connect", 0)
                self.view.set_u8("reset_req", 1)

        self.gw.on_pend_irq = firmware_irq

        self.abi.connect()
        self.abi.wait_for_port_reset(timeout_s=0.2)
        self.abi.accept_port_reset()

        self.assertEqual(self.view.get_u8("reset_req"), 0)
        self.assertEqual(self.view.get_u8("port_enabled"), 1)
        self.assertEqual(self.view.get_u8("connected"), 1)
        self.assertEqual(self.gw.pended, [OTG_FS_IRQn, OTG_FS_IRQn])


class TransferServicingTest(unittest.TestCase):
    """wait_for_out / provide_in / ack_out / channel_info."""

    def setUp(self) -> None:
        self.layout = DEFAULT_USBH_SIM_LAYOUT
        self.base = USBH_SIM_BASE
        self.gw = FakeMmbGateway()
        self.view = MmbView(self.gw, self.base, self.layout)
        self.abi = Stm32OtgFsHostAbi(self.gw, base_address=self.base, layout=self.layout)
        # The middleware opens the two control pipes before enumerating.
        self.view.open_pipe(CTRL_OUT_CH, 0x00, EP_TYPE_CTRL, EP_DIR_OUT)
        self.view.open_pipe(CTRL_IN_CH, 0x80, EP_TYPE_CTRL, EP_DIR_IN)

    def _arm_setup(self, ch: int, setup: bytes) -> None:
        """Mimic USBH_LL_SubmitURB(OUT): payload in out_buf, out_pending=1."""
        self.view.ch_write(ch, "out_buf", setup)
        self.view.ch_set_u16(ch, "out_len", len(setup))
        self.view.ch_set_u8(ch, "urb_state", URB_IDLE)
        self.view.ch_set_u8(ch, "out_pending", 1)

    # -- channel_info --------------------------------------------------------

    def test_channel_info_reads_endpoint_identity(self) -> None:
        self.view.open_pipe(BULK_OUT_CH, 0x01, EP_TYPE_BULK, EP_DIR_OUT)
        self.view.open_pipe(BULK_IN_CH, 0x81, EP_TYPE_BULK, EP_DIR_IN)

        ctrl_out = self.abi.channel_info(CTRL_OUT_CH)
        self.assertEqual((ctrl_out.endpoint, ctrl_out.ep_type, ctrl_out.direction),
                         (0x00, EP_TYPE_CTRL, EP_DIR_OUT))

        bulk_in = self.abi.channel_info(BULK_IN_CH)
        self.assertEqual((bulk_in.endpoint, bulk_in.ep_type, bulk_in.direction),
                         (0x81, EP_TYPE_BULK, EP_DIR_IN))

    # -- wait_for_out --------------------------------------------------------

    def test_wait_for_out_returns_first_setup(self) -> None:
        """The first SETUP must read back as 80 06 00 01 00 00 08 00."""
        self._arm_setup(CTRL_OUT_CH, GET_DESCRIPTOR_DEVICE_8)

        pkt = self.abi.wait_for_out((CTRL_OUT_CH, BULK_OUT_CH), timeout_s=0.2)

        self.assertIsInstance(pkt, UsbOutPacket)
        self.assertEqual(pkt.channel, CTRL_OUT_CH)
        self.assertEqual(pkt.endpoint, 0x00)
        self.assertTrue(pkt.is_setup)
        self.assertEqual(pkt.data, GET_DESCRIPTOR_DEVICE_8)
        self.assertEqual(pkt.data.hex(" "), "80 06 00 01 00 00 08 00")
        # wait_for_out must not consume the request -- ack_out does that.
        self.assertEqual(self.view.ch_get_u8(CTRL_OUT_CH, "out_pending"), 1)

    def test_wait_for_out_zero_length_status_is_not_setup(self) -> None:
        self._arm_setup(CTRL_OUT_CH, b"")  # zero-length status OUT
        pkt = self.abi.wait_for_out(CTRL_OUT_CH, timeout_s=0.2)
        self.assertFalse(pkt.is_setup)
        self.assertEqual(pkt.data, b"")

    def test_wait_for_out_bulk_data_is_not_setup(self) -> None:
        self.view.open_pipe(BULK_OUT_CH, 0x01, EP_TYPE_BULK, EP_DIR_OUT)
        self._arm_setup(BULK_OUT_CH, b"ping")  # 4-byte bulk OUT payload
        pkt = self.abi.wait_for_out((CTRL_OUT_CH, BULK_OUT_CH), timeout_s=0.2)
        self.assertEqual(pkt.channel, BULK_OUT_CH)
        self.assertEqual(pkt.endpoint, 0x01)
        self.assertFalse(pkt.is_setup)
        self.assertEqual(pkt.data, b"ping")

    def test_wait_for_out_times_out_when_idle(self) -> None:
        with self.assertRaises(TargetTimeoutError):
            self.abi.wait_for_out(CTRL_OUT_CH, timeout_s=0.05)

    # -- ack_out (ordering rule) ---------------------------------------------

    def test_ack_out_clears_pending_then_marks_done_last(self) -> None:
        self._arm_setup(CTRL_OUT_CH, GET_DESCRIPTOR_DEVICE_8)
        self.gw.writes.clear()

        self.abi.ack_out(CTRL_OUT_CH)

        self.assertEqual(self.view.ch_get_u8(CTRL_OUT_CH, "out_pending"), 0)
        self.assertEqual(self.view.ch_get_u8(CTRL_OUT_CH, "urb_state"), URB_DONE)
        # urb_state=DONE must be the LAST write (DONE-last ordering rule).
        last_addr, _ = self.gw.writes[-1]
        self.assertEqual(last_addr, self.view.ch_addr(CTRL_OUT_CH, "urb_state"))
        # ...and out_pending must have been cleared before it.
        outp_idx = self._first_write_index(self.view.ch_addr(CTRL_OUT_CH, "out_pending"))
        done_idx = self._first_write_index(self.view.ch_addr(CTRL_OUT_CH, "urb_state"))
        self.assertLess(outp_idx, done_idx)

    # -- provide_in (delivery + ordering rule) -------------------------------

    def test_provide_in_delivers_bytes_and_marks_done_last(self) -> None:
        data = CDC_DEVICE_DESCRIPTOR[:8]
        self.view.ch_set_u8(CTRL_IN_CH, "in_requested", 1)
        self.view.ch_set_u8(CTRL_IN_CH, "urb_state", URB_IDLE)
        self.gw.writes.clear()

        self.abi.provide_in(CTRL_IN_CH, data, timeout_s=0.2)

        self.assertEqual(self.view.ch_read(CTRL_IN_CH, "in_buf", len(data)), data)
        self.assertEqual(self.view.ch_get_u16(CTRL_IN_CH, "xfer_count"), len(data))
        self.assertEqual(self.view.ch_get_u8(CTRL_IN_CH, "in_requested"), 0)
        self.assertEqual(self.view.ch_get_u8(CTRL_IN_CH, "urb_state"), URB_DONE)
        # urb_state=DONE last; everything else (payload, count, in_requested=0) before.
        last_addr, _ = self.gw.writes[-1]
        self.assertEqual(last_addr, self.view.ch_addr(CTRL_IN_CH, "urb_state"))
        done_idx = self._first_write_index(self.view.ch_addr(CTRL_IN_CH, "urb_state"))
        for field in ("in_buf", "xfer_count", "in_requested"):
            self.assertLess(
                self._first_write_index(self.view.ch_addr(CTRL_IN_CH, field)),
                done_idx,
                f"{field} must be written before urb_state=DONE",
            )

    def test_provide_in_times_out_without_request(self) -> None:
        # in_requested stays 0 -> provide_in must time out waiting for the IN.
        with self.assertRaises(TargetTimeoutError):
            self.abi.provide_in(CTRL_IN_CH, b"\x12\x01", timeout_s=0.05)

    def test_provide_in_rejects_oversize_payload(self) -> None:
        self.view.ch_set_u8(CTRL_IN_CH, "in_requested", 1)
        with self.assertRaises(ValueError):
            self.abi.provide_in(CTRL_IN_CH, b"\x00" * (BUF_SIZE + 1), timeout_s=0.2)

    # -- helpers -------------------------------------------------------------

    def _first_write_index(self, address: int) -> int:
        for i, (addr, _) in enumerate(self.gw.writes):
            if addr == address:
                return i
        return -1


if __name__ == "__main__":
    unittest.main()
