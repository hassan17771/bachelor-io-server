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
    USBH_SIM_MAGIC,
    resolve_usbh_sim_base,
)
from target.gateway import TargetError

# First SETUP the CDC enumeration emits: GET_DESCRIPTOR(Device, length=8).
#   bmRequestType=0x80 bRequest=0x06 wValue=0x0100 wIndex=0x0000 wLength=0x0008
GET_DESCRIPTOR_DEVICE_SETUP = bytes([0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x08, 0x00])

NVIC_ISER_BASE = 0xE000E100

PORT_SPEED_FS = 1


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return default if value is None else int(value, 0)


class UsbHostConnectionTest(unittest.TestCase):
    """Issue a USB connection to the simulated OTG_FS host and watch the
    firmware react over SWD/J-Link.

    This is a hardware test: it needs firmware built with -DUSB_HOST_SIMULATION
    running on the target, and a JLinkGDBServerCLExe already attached, e.g.

        JLinkGDBServerCLExe -device STM32F411CE -if SWD -speed 4000 -port 2331

    Enable it with WCET_USB_HOST_TEST=1; it skips otherwise so the offline test
    suite stays green. Configuration via env:

        WCET_USB_HOST_TEST     1 to run (else skip)
        WCET_USB_HOST_BASE     MMB base address (default 0x2001E000)
        WCET_JLINK_HOST        GDB server host (default 127.0.0.1)
        WCET_JLINK_PORT        GDB server port (default 2331)
        WCET_USB_HOST_TIMEOUT  per-step wait, seconds (default 2.0)
    """

    def setUp(self) -> None:
        if not _truthy(os.environ.get("WCET_USB_HOST_TEST")):
            raise unittest.SkipTest("set WCET_USB_HOST_TEST=1 to run the USB host hardware test")

        self.layout = DEFAULT_USBH_SIM_LAYOUT
        self.base = resolve_usbh_sim_base()
        self.timeout_s = float(os.environ.get("WCET_USB_HOST_TIMEOUT", "2.0"))

        config = JLinkConnectionConfig(
            host=os.environ.get("WCET_JLINK_HOST", "127.0.0.1"),
            gdb_port=_env_int("WCET_JLINK_PORT", 2331),
            timeout_s=float(os.environ.get("WCET_JLINK_TIMEOUT", "2.0")),
        )
        self.target = JLinkBackend(config).connect()

        # The ISR and USBH_Process only run while the core is executing, and the
        # OTG_FS line must be enabled in the NVIC for pend_irq to take effect
        # (in sim mode firmware never calls HAL_HCD_MspInit, which would do it).
        self._ensure_running()
        self._enable_irq(OTG_FS_IRQn)

    def tearDown(self) -> None:
        target = getattr(self, "target", None)
        if target is not None:
            target.close()

    # -- MMB / NVIC helpers --------------------------------------------------

    def _hdr(self, field: str) -> int:
        return self.base + getattr(self.layout, field)

    def _ch(self, n: int, field: str) -> int:
        return self.base + self.layout.ch(n, field)

    def _ensure_running(self) -> None:
        # Best effort: "go" is harmless if the target is already free-running,
        # and some server setups manage run-state themselves.
        try:
            self.target.monitor_command("go")
        except TargetError:
            pass

    def _enable_irq(self, irqn: int) -> None:
        iser = NVIC_ISER_BASE + (irqn // 32) * 4
        self.target.write_u32(iser, 1 << (irqn % 32))

    def _issue_connect(self) -> None:
        """Publish a full-speed device and fire the connect event."""
        t = self.target
        t.write_u8(self._hdr("port_speed"), PORT_SPEED_FS)
        t.write_u8(self._hdr("connected"), 1)
        t.write_u8(self._hdr("ev_connect"), 1)
        t.pend_irq(OTG_FS_IRQn)

    # -- tests ---------------------------------------------------------------

    def test_connect_event_is_consumed(self) -> None:
        """Smoke test for the IRQ path (firmware tasks 2.1 + 2.2).

        Setting ev_connect and pending OTG_FS_IRQn must make the running
        firmware execute usbh_sim_irq_handler(), which clears ev_connect after
        delivering USBH_LL_Connect. Observing the flag return to 0 proves the
        event was dispatched -- independent of the later USBH_LL_* patches.
        """
        t = self.target
        self._issue_connect()
        t.wait_until(
            lambda: t.read_u8(self._hdr("ev_connect")) == 0,
            timeout_s=self.timeout_s,
            description="firmware to consume ev_connect (usbh_sim_irq_handler)",
        )

    def test_connection_triggers_port_reset_and_first_setup(self) -> None:
        """Full connect handshake through to the first enumeration SETUP.

        connect -> middleware requests a port reset -> accept the reset ->
        the stack opens EP0 and submits GET_DESCRIPTOR(Device, 8) on channel 0.

        Requires the USBH_LL_* sim patches (firmware tasks 2.3 / 2.4). Until
        those land, USBH_LL_Start never writes the magic and USBH_LL_ResetPort
        never raises reset_req, so this skips / fails at the first such step
        with a message pointing at the missing firmware.
        """
        t = self.target

        magic = t.read_u32(self._hdr("magic"))
        if magic != USBH_SIM_MAGIC:
            self.skipTest(
                f"MMB magic 0x{magic:08X} != 0x{USBH_SIM_MAGIC:08X}: build with "
                "-DUSB_HOST_SIMULATION and ensure USBH_LL_Start has run (task 2.3)"
            )

        # 1) Issue the connection.
        self._issue_connect()

        # 2) The middleware asks the LL driver to reset the port; the sim
        #    USBH_LL_ResetPort (task 2.3) records that as reset_req.
        t.wait_until(
            lambda: t.read_u8(self._hdr("reset_req")) == 1,
            timeout_s=self.timeout_s,
            description="USBH_LL_ResetPort to set reset_req",
        )

        # 3) Accept the reset: clear the request, bring the port up at FS, and
        #    notify the stack that the port is enabled.
        t.write_u8(self._hdr("reset_req"), 0)
        t.write_u8(self._hdr("port_enabled"), 1)
        t.write_u8(self._hdr("ev_port_en"), 1)
        t.pend_irq(OTG_FS_IRQn)

        # 4) Enumeration starts: the first SETUP lands on channel 0.
        t.wait_until(
            lambda: t.read_u8(self._ch(0, "out_pending")) == 1,
            timeout_s=self.timeout_s,
            description="first SETUP packet pending on channel 0",
        )
        setup = t.read(self._ch(0, "out_buf"), 8)
        self.assertEqual(
            setup,
            GET_DESCRIPTOR_DEVICE_SETUP,
            f"unexpected first SETUP packet: {setup.hex(' ')}",
        )


if __name__ == "__main__":
    unittest.main()
