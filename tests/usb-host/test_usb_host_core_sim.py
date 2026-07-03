"""UsbHostCore's control-transfer engine against an in-memory MMB.

Proves the engine is class-agnostic (Task 2.3 of the restructure plan): every
request in these scripts is vendor-type, the responder is a plain dict lookup,
and nothing here imports the CDC driver. The host side is played by
ScriptedHost, which arms each stage of a control transfer in reaction to the
mock device completing the previous one -- exactly the SETUP -> DATA -> STATUS
choreography the STM32 middleware drives over the MMB.

Runs with no J-Link and no target.
"""
from __future__ import annotations

import os
import sys
import unittest

# 'tests/usb-host' has a hyphen, so it is not an importable package. Put the
# wcet-io-server root (three levels up) on sys.path to run this file directly.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fake_mmb_gateway import FakeMmbGateway, MmbView
from firmware_abi.usb.host.stm32_otgfs import (
    DEFAULT_USBH_SIM_LAYOUT,
    EP_DIR_IN,
    EP_DIR_OUT,
    EP_TYPE_BULK,
    EP_TYPE_CTRL,
    URB_DONE,
    URB_IDLE,
    USBH_SIM_BASE,
)
from peripherals.usb.host.core import ControlTransferLog, UsbHostCore
from peripherals.usb.host.backends.stm32_otgfs import Stm32OtgFsHostAbi

CTRL_OUT_CH = 0
CTRL_IN_CH = 1
BULK_OUT_CH = 3
BULK_IN_CH = 4

REQ_DIR_DEVICE_TO_HOST = 0x80


def _setup_packet(bm_request_type: int, b_request: int, w_length: int) -> bytes:
    return bytes([bm_request_type, b_request, 0, 0, 0, 0,
                  w_length & 0xFF, (w_length >> 8) & 0xFF])


# Vendor-type requests only: a class driver would not recognise any of these.
VENDOR_READ_16 = _setup_packet(0xC0, 0x01, 16)   # device->host, 16-byte data stage
VENDOR_WRITE_7 = _setup_packet(0x40, 0x02, 7)    # host->device, 7-byte data stage
VENDOR_WRITE_0 = _setup_packet(0x40, 0x03, 0)    # host->device, no data stage
VENDOR_READ_80 = _setup_packet(0xC0, 0x04, 80)   # device->host, exceeds one URB


class ScriptedHost:
    """Plays the target's host stack over the fake MMB.

    ``transfers`` is a list of ``(setup, out_payload)``; ``out_payload`` is the
    host->device data stage (None when the transfer has none). Each stage is
    armed only after the device completes the previous one, mirroring the
    middleware's control FSM.
    """

    def __init__(self, view: MmbView, transfers) -> None:
        self.view = view
        self.transfers = list(transfers)
        self.idx = -1
        self.stage = None
        self.data_in: list[bytes] = []   # device->host data stages, as received
        self.status_in = 0               # zero-length STATUS INs received
        self.finished = 0                # transfers fully completed

    def start(self) -> None:
        self._next()

    def _next(self) -> None:
        self.idx += 1
        if self.idx >= len(self.transfers):
            self.stage = None
            return
        setup, _ = self.transfers[self.idx]
        self.stage = "setup"
        self._arm_out(setup)

    def _arm_out(self, payload: bytes) -> None:
        if payload:
            self.view.ch_write(CTRL_OUT_CH, "out_buf", payload)
        self.view.ch_set_u16(CTRL_OUT_CH, "out_len", len(payload))
        self.view.ch_set_u8(CTRL_OUT_CH, "urb_state", URB_IDLE)
        self.view.ch_set_u8(CTRL_OUT_CH, "out_pending", 1)

    def _arm_in(self) -> None:
        self.view.ch_set_u8(CTRL_IN_CH, "urb_state", URB_IDLE)
        self.view.ch_set_u8(CTRL_IN_CH, "in_requested", 1)

    def on_done(self, ch: int) -> None:
        """React to the device writing urb_state=DONE on a control pipe."""
        if self.idx >= len(self.transfers):
            return
        setup, out_payload = self.transfers[self.idx]
        device_to_host = bool(setup[0] & REQ_DIR_DEVICE_TO_HOST)
        w_length = setup[6] | (setup[7] << 8)

        if ch == CTRL_OUT_CH:
            if self.stage == "setup":
                if w_length == 0:
                    if device_to_host:
                        self.stage = "status_out"
                        self._arm_out(b"")
                    else:
                        self.stage = "status_in"
                        self._arm_in()
                elif device_to_host:
                    self.stage = "data_in"
                    self._arm_in()
                else:
                    self.stage = "data_out"
                    self._arm_out(out_payload)
            elif self.stage == "data_out":
                self.stage = "status_in"
                self._arm_in()
            elif self.stage == "status_out":
                self.finished += 1
                self._next()
        elif ch == CTRL_IN_CH:
            if self.stage == "data_in":
                n = self.view.ch_get_u16(CTRL_IN_CH, "xfer_count")
                self.data_in.append(self.view.ch_read(CTRL_IN_CH, "in_buf", n))
                self.stage = "status_out"
                self._arm_out(b"")
            elif self.stage == "status_in":
                self.status_in += 1
                self.finished += 1
                self._next()


class ScriptedHostGateway(FakeMmbGateway):
    """FakeMmbGateway that hands urb_state=DONE writes to a ScriptedHost."""

    def __init__(self) -> None:
        super().__init__()
        self.host: ScriptedHost | None = None
        self._done_addrs: dict[int, int] = {}
        self._reacting = False

    def watch(self, host: ScriptedHost) -> None:
        self.host = host
        self._done_addrs = {
            host.view.ch_addr(ch, "urb_state"): ch for ch in (CTRL_OUT_CH, CTRL_IN_CH)
        }

    def _write(self, address: int, data: bytes) -> None:
        super()._write(address, data)
        if self.host is None or self._reacting:
            return
        ch = self._done_addrs.get(address)
        if ch is not None and data == bytes([URB_DONE]):
            self._reacting = True
            try:
                self.host.on_done(ch)
            finally:
                self._reacting = False


class UsbHostCoreControlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gw = ScriptedHostGateway()
        self.view = MmbView(self.gw, USBH_SIM_BASE, DEFAULT_USBH_SIM_LAYOUT)
        abi = Stm32OtgFsHostAbi(self.gw, base_address=USBH_SIM_BASE,
                                layout=DEFAULT_USBH_SIM_LAYOUT)
        self.core = UsbHostCore(abi)
        self.view.open_pipe(CTRL_OUT_CH, 0x00, EP_TYPE_CTRL, EP_DIR_OUT)
        self.view.open_pipe(CTRL_IN_CH, 0x80, EP_TYPE_CTRL, EP_DIR_IN)

        # The stub responder: a dict lookup on the raw SETUP, no USB knowledge.
        self.replies = {
            VENDOR_READ_16: bytes(range(16)),
            VENDOR_READ_80: bytes(range(80)),
        }
        self.responder_setups: list[bytes] = []

    def _responder(self, setup: bytes) -> bytes:
        self.responder_setups.append(bytes(setup))
        return self.replies.get(bytes(setup), b"")

    def _run_script(self, transfers, **kwargs) -> tuple[ScriptedHost, ControlTransferLog]:
        host = ScriptedHost(self.view, transfers)
        self.gw.watch(host)
        host.start()
        want = len(transfers)
        log = self.core.service_control_transfers(
            self._responder,
            done=lambda log: len(log.setups) == want and host.finished == want,
            timeout_s=kwargs.pop("timeout_s", 5.0),
            setup_timeout_s=0.05,
            **kwargs,
        )
        return host, log

    def test_services_mixed_vendor_control_transfers(self) -> None:
        host, log = self._run_script([
            (VENDOR_READ_16, None),
            (VENDOR_WRITE_7, b"payload"),
            (VENDOR_WRITE_0, None),
        ])

        self.assertTrue(log.completed)
        self.assertEqual(log.setups, [VENDOR_READ_16, VENDOR_WRITE_7, VENDOR_WRITE_0])
        self.assertFalse(log.truncated)
        # device->host data stage delivered intact...
        self.assertEqual(host.data_in, [bytes(range(16))])
        # ...host->device data stage captured intact...
        self.assertEqual(log.data_out, [b"payload"])
        # ...and every transfer's status stage was serviced.
        self.assertEqual(host.finished, 3)
        self.assertEqual(host.status_in, 2)  # the two host->device transfers
        # The responder saw only the device->host request with a data stage.
        self.assertEqual(self.responder_setups, [VENDOR_READ_16])

    def test_truncates_data_stage_to_one_urb(self) -> None:
        host, log = self._run_script([(VENDOR_READ_80, None)])

        self.assertTrue(log.completed)
        self.assertTrue(log.truncated)
        self.assertEqual(host.data_in, [bytes(range(64))])

    def test_returns_incomplete_when_nothing_happens(self) -> None:
        log = self.core.service_control_transfers(
            self._responder, done=lambda log: False,
            timeout_s=0.15, setup_timeout_s=0.05,
        )
        self.assertFalse(log.completed)
        self.assertEqual(log.setups, [])

    def test_done_is_checked_before_the_first_wait(self) -> None:
        log = self.core.service_control_transfers(
            self._responder, done=lambda log: True, timeout_s=5.0,
        )
        self.assertTrue(log.completed)
        self.assertEqual(log.setups, [])

    def test_stray_non_setup_out_is_acked_and_skipped(self) -> None:
        # A zero-length OUT with no preceding SETUP: not a control transfer.
        self.view.ch_set_u16(CTRL_OUT_CH, "out_len", 0)
        self.view.ch_set_u8(CTRL_OUT_CH, "urb_state", URB_IDLE)
        self.view.ch_set_u8(CTRL_OUT_CH, "out_pending", 1)

        log = self.core.service_control_transfers(
            self._responder, done=None, timeout_s=0.15, setup_timeout_s=0.05,
        )

        self.assertEqual(log.setups, [])
        self.assertEqual(self.view.ch_get_u8(CTRL_OUT_CH, "out_pending"), 0)
        self.assertEqual(self.view.ch_get_u8(CTRL_OUT_CH, "urb_state"), URB_DONE)


class UsbHostCoreBulkTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gw = FakeMmbGateway()
        self.view = MmbView(self.gw, USBH_SIM_BASE, DEFAULT_USBH_SIM_LAYOUT)
        abi = Stm32OtgFsHostAbi(self.gw, base_address=USBH_SIM_BASE,
                                layout=DEFAULT_USBH_SIM_LAYOUT)
        self.core = UsbHostCore(abi)
        self.view.open_pipe(BULK_OUT_CH, 0x01, EP_TYPE_BULK, EP_DIR_OUT)
        self.view.open_pipe(BULK_IN_CH, 0x81, EP_TYPE_BULK, EP_DIR_IN)

    def test_bulk_out_returns_payload_and_acks(self) -> None:
        self.view.ch_write(BULK_OUT_CH, "out_buf", b"ping")
        self.view.ch_set_u16(BULK_OUT_CH, "out_len", 4)
        self.view.ch_set_u8(BULK_OUT_CH, "urb_state", URB_IDLE)
        self.view.ch_set_u8(BULK_OUT_CH, "out_pending", 1)

        data = self.core.bulk_out(BULK_OUT_CH, timeout_s=0.2)

        self.assertEqual(data, b"ping")
        self.assertEqual(self.view.ch_get_u8(BULK_OUT_CH, "out_pending"), 0)
        self.assertEqual(self.view.ch_get_u8(BULK_OUT_CH, "urb_state"), URB_DONE)

    def test_bulk_in_delivers_reply(self) -> None:
        self.view.ch_set_u8(BULK_IN_CH, "urb_state", URB_IDLE)
        self.view.ch_set_u8(BULK_IN_CH, "in_requested", 1)

        self.core.bulk_in(BULK_IN_CH, b"pong!", timeout_s=0.2)

        self.assertEqual(self.view.ch_read(BULK_IN_CH, "in_buf", 5), b"pong!")
        self.assertEqual(self.view.ch_get_u16(BULK_IN_CH, "xfer_count"), 5)
        self.assertEqual(self.view.ch_get_u8(BULK_IN_CH, "urb_state"), URB_DONE)

    def test_bulk_in_rejects_more_than_one_urb(self) -> None:
        with self.assertRaises(ValueError):
            self.core.bulk_in(BULK_IN_CH, b"\x00" * 65, timeout_s=0.2)


if __name__ == "__main__":
    unittest.main()
