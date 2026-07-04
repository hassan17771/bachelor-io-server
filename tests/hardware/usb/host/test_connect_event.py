"""Device-connect interrupt delivery on the real target.

Issues *only* a device-connect event and confirms the firmware acts on it -- a
focused check of usbh_sim_irq_handler and the OTG_FS_IRQHandler patch. Nothing
here drives port reset or enumeration; that depends on the USBH_LL_* patches
covered by the other tests in this directory.

Needs firmware built with -DUSB_HOST_SIMULATION running on the target and a
JLinkGDBServerCLExe already attached, e.g.

    JLinkGDBServerCLExe -device STM32F411CE -if SWD -speed 4000 -port 2331

Enable with WCET_USB_HOST_TEST=1 (skips otherwise); see
tests/support/hardware.py for the full environment-variable table.
"""
from __future__ import annotations

import unittest

from tests.support.hardware import UsbHostHardwareTest


class UsbHostConnectionTest(UsbHostHardwareTest):
    # The ISR check needs no fresh boot and must not assume the host stack has
    # published the magic yet; keep the original light-weight bring-up.
    RESET_ON_SETUP = False
    WAIT_FOR_MAGIC = False
    DEFAULT_TIMEOUT_S = 2.0

    def test_issue_connection(self) -> None:
        """Make the STM32 believe a full-speed device just connected.

        Publish the device (connected=1, port_speed=FS), raise the connect event
        (ev_connect=1) and pend OTG_FS_IRQn -- exactly what abi.connect() does.
        The running firmware then executes usbh_sim_irq_handler(), which
        delivers USBH_LL_Connect to the host stack and clears ev_connect.
        Seeing ev_connect return to 0 proves the interrupt was taken and the
        connect event was dispatched.
        """
        # Sanity: the event flag starts clear so the 0 we wait for is the
        # firmware's doing, not a stale value.
        self.view.set_u8("ev_connect", 0)

        self.abi.connect()

        # The ISR consumes the event: ev_connect goes back to 0.
        self.target.wait_until(
            lambda: self.view.get_u8("ev_connect") == 0,
            timeout_s=self.timeout_s,
            description="firmware to consume ev_connect (usbh_sim_irq_handler -> USBH_LL_Connect)",
        )


if __name__ == "__main__":
    unittest.main()
