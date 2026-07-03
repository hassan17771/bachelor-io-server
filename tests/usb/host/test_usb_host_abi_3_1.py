"""Stm32OtgFsHostAbi lifecycle/port methods.

Verifies validate / connect / disconnect / wait_for_port_reset /
accept_port_reset against an in-memory MMB (tests.support.fake_gateway), so it
runs with no J-Link and no target attached:

    cd wcet-io-server
    python -m pytest tests/usb/host/test_usb_host_abi_3_1.py

These assert the *Python-side* transport contract -- the exact fields each call
writes and the events it pends. The matching firmware behaviour is covered by
the live tests/usb/host/test_usbh_conf_sim.py.
"""
from __future__ import annotations

import unittest

from tests.support.fake_gateway import FakeMmbGateway, MmbView

from firmware_abi.usb.host.stm32_otgfs import (
    BLOCK_SIZE,
    DEFAULT_USBH_SIM_LAYOUT,
    OTG_FS_IRQn,
    USBH_SIM_BASE,
    USBH_SIM_MAGIC,
    USBH_SIM_VERSION,
)
from peripherals.usb.common.requests import SPEED_FULL
from peripherals.usb.host.abi import UsbHostSimError
from peripherals.usb.host.backends.stm32_otgfs import Stm32OtgFsHostAbi
from target.gateway import TargetTimeoutError


class Abi31Test(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
