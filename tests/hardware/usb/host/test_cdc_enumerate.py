"""Full CDC enumeration against the real target over SWD/J-Link.

This is a HARDWARE test: it drives the actual STM32 USB-host middleware running
on the target (firmware built with -DUSB_HOST_SIMULATION). It brings
the simulated port up, runs UsbHostCdcDevice.enumerate(), and asserts the
firmware reached APPLICATION_READY -- i.e. enumeration completed AND the CDC
class went active (HOST_USER_CLASS_ACTIVE). Nothing here is mocked.

    # start the J-Link GDB server first, e.g.
    #   JLinkGDBServerCLExe -device STM32F411CE -if SWD -speed 4000 -port 2331
    cd wcet-io-server
    WCET_USB_HOST_TEST=1 python -m pytest tests/hardware/usb/host/test_cdc_enumerate.py

The enumerate() completion predicate reads the firmware global `Appli_state`
(usb_host.c); see tests.support.hardware.resolve_appli_state for how its
address is found (WCET_APPLI_STATE_ADDR / WCET_USB_HOST_ELF / the project's
Release/Debug builds) and for the full environment-variable table.
"""
from __future__ import annotations

import unittest

from peripherals.usb.common.requests import SPEED_FULL
from peripherals.usb.host.cdc import UsbHostCdcDevice
from tests.support.hardware import (
    APPLI_STATE_NAMES,
    APPLICATION_READY,
    UsbHostHardwareTest,
    env_float,
)

# The first SETUP a CDC enumeration emits: GET_DESCRIPTOR(Device, wLength=8).
GET_DESCRIPTOR_DEVICE_8 = bytes([0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x08, 0x00])


class UsbHostCdcEnumerateTest(UsbHostHardwareTest):
    RESOLVE_APPLI_STATE = True

    def test_enumerate_reaches_application_ready(self) -> None:
        device = UsbHostCdcDevice(self.abi)

        # Attach the simulated device and bring the port up.
        device.attach(speed=SPEED_FULL, timeout_s=self.timeout_s)

        # Enumeration has not run yet, so the CDC class cannot be active.
        self.assertNotEqual(
            self.appli_state(), APPLICATION_READY,
            "host reported APPLICATION_READY before enumeration even started",
        )

        # Service the whole enumeration; finish when the firmware signals the CDC
        # class is active (APPLICATION_READY).
        result = device.enumerate(
            done=lambda: self.appli_state() == APPLICATION_READY,
            timeout_s=env_float("WCET_USB_HOST_ENUM_TIMEOUT", 20.0),
        )

        final = self.appli_state()
        print(
            "enumeration: "
            + result.summary()
            + f"\n  Appli_state = {final} ({APPLI_STATE_NAMES.get(final, '?')})"
            + f"\n  SETUPs serviced ({len(result.setups)}):"
        )
        for s in result.setups:
            print("    " + s.hex(" "))

        # Enumeration completed and the CDC class went active.
        self.assertEqual(
            final, APPLICATION_READY,
            f"Appli_state did not reach APPLICATION_READY (got {final}); {result.summary()}",
        )
        self.assertTrue(result.completed)

        # Sanity on the sequence the stack actually drove.
        self.assertEqual(
            result.setups[0], GET_DESCRIPTOR_DEVICE_8,
            f"first SETUP was {result.setups[0].hex(' ')}, expected GET_DESCRIPTOR(Device,8)",
        )
        self.assertEqual(result.address, 1, "host should have assigned device address 1")
        self.assertTrue(result.set_configuration, "host should have issued SET_CONFIGURATION")
        self.assertTrue(result.got_line_coding, "CDC class should have issued GET_LINE_CODING")

        # With the data pipes now open, the CDC bulk endpoints should be visible.
        bulk_in = self.abi.channel_info(4)
        self.assertEqual(bulk_in.endpoint, 0x81, "channel 4 should be the CDC bulk IN endpoint")


if __name__ == "__main__":
    unittest.main()
