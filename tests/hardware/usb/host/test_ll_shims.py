"""USBH_LL_* sim shims, observed through the MMB over SWD/J-Link.

Covered:

    USBH_LL_Init      -> the block is memset to 0 at boot
    USBH_LL_Start     -> magic / version / block_size published
    USBH_LL_ResetPort -> reset_req raised when the stack resets the port
    USBH_LL_OpenPipe  -> EP0 control pipe identities recorded in ch[0]/ch[1]

Not covered (not observable here, and outside "no full enumeration"):

    USBH_LL_GetSpeed  -> result goes to phost->device.speed, not the MMB
                         (it does run, just before OpenPipe on the same path)
    USBH_LL_Stop      -> only called on USBH_Stop/DeInit (disconnect path)
    USBH_LL_ClosePipe -> only called on FreePipe (re-enum / disconnect path)

Needs firmware built with -DUSB_HOST_SIMULATION running on the target, plus a
JLinkGDBServerCLExe already attached. Enable with WCET_USB_HOST_TEST=1 (skips
otherwise); see tests/support/hardware.py for the environment-variable table.

Every case starts from a fresh boot (the base class resets): it isolates the
cases and recovers from the HardFault the OpenPipe case provokes (the unpatched
USBH_LL_SubmitURB runs once OpenPipe has finished -- see that test).
"""
from __future__ import annotations

import unittest

from firmware_abi.usb.host.stm32_otgfs import (
    BLOCK_SIZE,
    EP_DIR_IN,
    EP_DIR_OUT,
    EP_TYPE_CTRL,
    NUM_CH,
    USBH_SIM_MAGIC,
    USBH_SIM_VERSION,
)
from tests.support.hardware import UsbHostHardwareTest


class UsbhLlShimsTest(UsbHostHardwareTest):
    # -- USBH_LL_Start -------------------------------------------------------

    def test_start_publishes_identity(self) -> None:
        """USBH_LL_Start writes version + block_size, then the magic."""
        status = self.abi.validate()
        self.assertEqual(status.magic, USBH_SIM_MAGIC)
        self.assertEqual(status.version, USBH_SIM_VERSION)
        self.assertEqual(status.block_size, BLOCK_SIZE)

    # -- USBH_LL_Init --------------------------------------------------------

    def test_init_cleared_the_block(self) -> None:
        """USBH_LL_Init memsets the MMB: before any connection the header flags
        and every channel read back as 0 (magic/version/block_size aside)."""
        for field in (
            "connected",
            "port_enabled",
            "port_speed",
            "reset_req",
            "ev_connect",
            "ev_disconnect",
            "ev_port_en",
        ):
            self.assertEqual(
                self.view.get_u8(field), 0, f"header.{field} not cleared by USBH_LL_Init"
            )

        for n in range(NUM_CH):
            for field in ("ep_addr", "ep_type", "ep_dir", "urb_state", "out_pending", "in_requested"):
                self.assertEqual(
                    self.view.ch_get_u8(n, field), 0, f"ch[{n}].{field} not cleared"
                )
            self.assertEqual(self.view.ch_get_u16(n, "out_len"), 0, f"ch[{n}].out_len not cleared")
            self.assertEqual(self.view.ch_get_u16(n, "xfer_count"), 0, f"ch[{n}].xfer_count not cleared")

    # -- USBH_LL_ResetPort ---------------------------------------------------

    def test_reset_port_raises_reset_req(self) -> None:
        """On connect the stack calls USBH_LL_ResetPort, which sets reset_req,
        then parks waiting for the port to come up -- so this never reaches the
        unpatched URB path."""
        self.assertEqual(self.view.get_u8("reset_req"), 0)

        self.abi.connect()
        self.abi.wait_for_port_reset(timeout_s=self.timeout_s)  # must not raise

    # -- USBH_LL_OpenPipe ----------------------------------------------------

    def test_open_pipe_records_control_endpoints(self) -> None:
        """After the port comes up, the stack opens the two EP0 control pipes
        via USBH_LL_OpenPipe before submitting any URB. We accept the reset,
        then read the recorded pipe identities.

        Note: one iteration later the stack submits the first SETUP through
        USBH_LL_SubmitURB. That happens *after* OpenPipe has written ch[0]/ch[1],
        and the records persist in RAM, so the assertions below are unaffected
        (J-Link reads memory regardless of core state; setUp resets next time).
        """
        self.abi.connect()
        self.abi.wait_for_port_reset(timeout_s=self.timeout_s)
        self.abi.accept_port_reset()

        # The control IN pipe (EP 0x80) is the detectable one -- the OUT pipe's
        # endpoint is 0x00, indistinguishable from a cleared channel.
        self.target.wait_until(
            lambda: self.view.ch_get_u8(1, "ep_addr") == 0x80,
            timeout_s=self.timeout_s,
            description="USBH_LL_OpenPipe to record the EP0 IN control pipe (ch[1])",
        )

        # ch[1] = EP0 control IN
        ctrl_in = self.abi.channel_info(1)
        self.assertEqual(ctrl_in.endpoint, 0x80)
        self.assertEqual(ctrl_in.ep_type, EP_TYPE_CTRL)
        self.assertEqual(ctrl_in.direction, EP_DIR_IN)

        # ch[0] = EP0 control OUT (ep_addr 0x00; direction is the real evidence)
        ctrl_out = self.abi.channel_info(0)
        self.assertEqual(ctrl_out.endpoint, 0x00)
        self.assertEqual(ctrl_out.ep_type, EP_TYPE_CTRL)
        self.assertEqual(ctrl_out.direction, EP_DIR_OUT)


if __name__ == "__main__":
    unittest.main()
