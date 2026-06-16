from __future__ import annotations

import os
import sys
import unittest

# This folder ('tests/usb-host') has a hyphen, so it is not an importable
# package and `python -m unittest discover` cannot traverse it. Put the
# wcet-io-server package root (three levels up) on sys.path so the test runs
# when invoked directly:  python tests/usb-host/test_usb_host_connection.py
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backends.jlink import JLinkBackend, JLinkConnectionConfig
from firmware_abi.usbh_otgfs import (
    DEFAULT_USBH_SIM_LAYOUT,
    OTG_FS_IRQn,
    resolve_usbh_sim_base,
)
from target.gateway import TargetError

NVIC_ISER_BASE = 0xE000E100
PORT_SPEED_FS = 1


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return default if value is None else int(value, 0)


class UsbHostConnectionTest(unittest.TestCase):
    """Issue *only* a device-connect interrupt and confirm the firmware acts on
    it -- a focused check of firmware tasks 2.1 (usbh_sim_irq_handler) and 2.2
    (the OTG_FS_IRQHandler patch). Nothing here drives port reset or
    enumeration; that depends on the later USBH_LL_* patches (tasks 2.3 / 2.4).

    Hardware test: needs firmware built with -DUSB_HOST_SIMULATION running on the
    target and a JLinkGDBServerCLExe already attached, e.g.

        JLinkGDBServerCLExe -device STM32F411CE -if SWD -speed 4000 -port 2331

    Enable with WCET_USB_HOST_TEST=1 (skips otherwise). Config via env:

        WCET_USB_HOST_TEST     1 to run (else skip)
        WCET_USB_HOST_BASE     MMB base address (default 0x2001E000)
        WCET_JLINK_HOST        GDB server host (default 127.0.0.1)
        WCET_JLINK_PORT        GDB server port (default 2331)
        WCET_USB_HOST_TIMEOUT  wait for the ISR, seconds (default 2.0)
    """

    def setUp(self) -> None:
        # if not _truthy(os.environ.get("WCET_USB_HOST_TEST")):
        #     raise unittest.SkipTest("set WCET_USB_HOST_TEST=1 to run the USB host hardware test")

        self.layout = DEFAULT_USBH_SIM_LAYOUT
        self.base = resolve_usbh_sim_base()
        self.timeout_s = float(os.environ.get("WCET_USB_HOST_TIMEOUT", "2.0"))

        config = JLinkConnectionConfig(
            host=os.environ.get("WCET_JLINK_HOST", "127.0.0.1"),
            gdb_port=_env_int("WCET_JLINK_PORT", 2331),
            timeout_s=float(os.environ.get("WCET_JLINK_TIMEOUT", "2.0")),
        )
        self.target = JLinkBackend(config).connect()

        # The ISR only runs while the core executes, and the OTG_FS line must be
        # enabled in the NVIC for pend_irq to take effect (in sim mode firmware
        # never calls HAL_HCD_MspInit, which would normally enable it).
        self._ensure_running()
        self._enable_irq(OTG_FS_IRQn)

    def tearDown(self) -> None:
        target = getattr(self, "target", None)
        if target is not None:
            target.close()

    def _hdr(self, field: str) -> int:
        return self.base + getattr(self.layout, field)

    def _ensure_running(self) -> None:
        # Best effort: "go" is harmless if the target is already free-running.
        try:
            self.target.monitor_command("go")
        except TargetError:
            pass

    def _enable_irq(self, irqn: int) -> None:
        iser = NVIC_ISER_BASE + (irqn // 32) * 4
        self.target.write_u32(iser, 1 << (irqn % 32))

    def test_issue_connection(self) -> None:
        """Make the STM32 believe a full-speed device just connected.

        Publish the device (connected=1, port_speed=FS), raise the connect event
        (ev_connect=1) and pend OTG_FS_IRQn. The running firmware then executes
        usbh_sim_irq_handler(), which delivers USBH_LL_Connect to the host stack
        and clears ev_connect. Seeing ev_connect return to 0 proves the
        interrupt was taken and the connect event was dispatched.
        """
        t = self.target

        # Sanity: the event flag starts clear so the 0 we wait for is the
        # firmware's doing, not a stale value.
        t.write_u8(self._hdr("ev_connect"), 0)

        # Issue the connection.
        t.write_u8(self._hdr("port_speed"), PORT_SPEED_FS)
        t.write_u8(self._hdr("connected"), 1)
        t.write_u8(self._hdr("ev_connect"), 1)
        t.pend_irq(OTG_FS_IRQn)

        # The ISR consumes the event: ev_connect goes back to 0.
        t.wait_until(
            lambda: t.read_u8(self._hdr("ev_connect")) == 0,
            timeout_s=self.timeout_s,
            description="firmware to consume ev_connect (usbh_sim_irq_handler -> USBH_LL_Connect)",
        )


if __name__ == "__main__":
    unittest.main()
