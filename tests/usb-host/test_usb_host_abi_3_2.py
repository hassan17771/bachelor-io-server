"""Stm32OtgFsHostAbi per-transfer servicing.

Verifies wait_for_out / provide_in / ack_out / channel_info against an
in-memory MMB (fake_mmb_gateway), so it runs with no J-Link and no target:

    cd wcet-io-server
    python tests/usb-host/test_usb_host_abi_3_2.py

Covers the first SETUP the IO server must read back (80 06 00 01 00 00 08 00)
plus the critical write-ordering rule: ack_out and provide_in must write
urb_state=DONE LAST.
"""
from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fake_mmb_gateway import FakeMmbGateway, MmbView

from firmware_abi.usbh_otgfs import (
    BUF_SIZE,
    CDC_DEVICE_DESCRIPTOR,
    DEFAULT_USBH_SIM_LAYOUT,
    EP_DIR_IN,
    EP_DIR_OUT,
    EP_TYPE_BULK,
    EP_TYPE_CTRL,
    URB_DONE,
    URB_IDLE,
    USBH_SIM_BASE,
)
from peripherals.usb_host import UsbOutPacket
from peripherals.usb_host_otgfs import Stm32OtgFsHostAbi
from target.gateway import TargetTimeoutError

# The first SETUP CDC enumeration emits: GET_DESCRIPTOR(Device, wLength=8).
GET_DESCRIPTOR_DEVICE_8 = bytes([0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x08, 0x00])

# Deterministic pipe allocation: ch0 EP0-OUT, ch1 EP0-IN,
# ch3 bulk-OUT EP0x01, ch4 bulk-IN EP0x81.
CTRL_OUT_CH = 0
CTRL_IN_CH = 1
BULK_OUT_CH = 3
BULK_IN_CH = 4


class Abi32Test(unittest.TestCase):
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
