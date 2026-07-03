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
(usb_host.c). Its address is resolved, in order, from:
  * WCET_APPLI_STATE_ADDR   (e.g. 0x200000f4)
  * WCET_USB_HOST_ELF       (arm-none-eabi-nm <elf> | grep Appli_state)
  * the project's Release/ then Debug/ blackUSB.elf
For the current Release build `Appli_state` is at 0x200000f4 (Debug: 0x200004cc).
"""
from __future__ import annotations

import os
import unittest

# The wcet-io-server root; its parent is the project root holding Debug/ and
# Release/ (the firmware build outputs used to resolve symbol addresses).
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
_PROJECT = os.path.dirname(_ROOT)

from backends.jlink import JLinkBackend, JLinkConnectionConfig
from firmware_abi.usb.host.stm32_otgfs import (
    DEFAULT_USBH_SIM_LAYOUT,
    OTG_FS_IRQn,
    USBH_SIM_MAGIC,
    resolve_usbh_sim_base,
)
from peripherals.usb.common.requests import SPEED_FULL
from peripherals.usb.host.cdc import UsbHostCdcDevice
from peripherals.usb.host.backends.stm32_otgfs import Stm32OtgFsHostAbi
from target.gateway import TargetError
from target.symbols import NmSymbolResolver

NVIC_ISER_BASE = 0xE000E100

# usb_host.c: APPLICATION_READY is enum value 2 (set on HOST_USER_CLASS_ACTIVE).
APPLICATION_IDLE = 0
APPLICATION_START = 1
APPLICATION_READY = 2
APPLICATION_DISCONNECT = 3
APPLI_STATE_NAMES = {0: "IDLE", 1: "START", 2: "READY", 3: "DISCONNECT"}

# The first SETUP a CDC enumeration emits: GET_DESCRIPTOR(Device, wLength=8).
GET_DESCRIPTOR_DEVICE_8 = bytes([0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x08, 0x00])


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return default if value is None else int(value, 0)


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


class UsbHostCdcEnumerateTest(unittest.TestCase):
    """Run the enumeration end-to-end on the target.

    Requires a JLinkGDBServerCLExe already attached to the STM32 and firmware
    flashed with the sim shims. Enable with WCET_USB_HOST_TEST=1 (skips
    otherwise), matching the other hardware tests in this directory.
    """

    def setUp(self) -> None:
        if not _truthy(os.environ.get("WCET_USB_HOST_TEST")):
            raise unittest.SkipTest("set WCET_USB_HOST_TEST=1 to run the USB host hardware test")

        self.layout = DEFAULT_USBH_SIM_LAYOUT
        self.base = resolve_usbh_sim_base()
        self.timeout_s = float(os.environ.get("WCET_USB_HOST_TIMEOUT", "3.0"))

        config = JLinkConnectionConfig(
            host=os.environ.get("WCET_JLINK_HOST", "127.0.0.1"),
            gdb_port=_env_int("WCET_JLINK_PORT", 2331),
            timeout_s=float(os.environ.get("WCET_JLINK_TIMEOUT", "2.0")),
        )
        self.target = JLinkBackend(config).connect()

        # Fresh boot for isolation, then make sure the host stack published magic.
        self.target.reset(halt_after=False)
        self._ensure_running()
        self._enable_irq(OTG_FS_IRQn)
        try:
            self.target.wait_until(
                lambda: self.target.read_u32(self.base + self.layout.magic) == USBH_SIM_MAGIC,
                timeout_s=self.timeout_s,
                description="USBH_LL_Start to publish the MMB magic",
            )
        except TargetError as exc:
            self.fail(
                f"host stack never published the magic ({exc}). Is the firmware "
                "built with -DUSB_HOST_SIMULATION and flashed?"
            )

        self.appli_state_addr = self._resolve_appli_state()

    def tearDown(self) -> None:
        target = getattr(self, "target", None)
        if target is not None:
            target.close()

    # -- helpers -------------------------------------------------------------

    def _ensure_running(self) -> None:
        try:
            self.target.monitor_command("go")
        except TargetError:
            pass

    def _enable_irq(self, irqn: int) -> None:
        iser = NVIC_ISER_BASE + (irqn // 32) * 4
        self.target.write_u32(iser, 1 << (irqn % 32))

    def _resolve_appli_state(self) -> int:
        raw = os.environ.get("WCET_APPLI_STATE_ADDR")
        if raw:
            return int(raw, 0)

        # Prefer the ELF given by env, else the project's Release/ then Debug/
        # build. For each, try arm-none-eabi-nm; if the toolchain is not on PATH,
        # fall back to parsing the sibling .map file (checked in, no tools needed).
        elf = os.environ.get("WCET_USB_HOST_ELF")
        builds = []
        if elf:
            builds.append((elf, os.path.splitext(elf)[0] + ".map"))
        for name in ("Release", "Debug"):
            builds.append(
                (os.path.join(_PROJECT, name, "blackUSB.elf"),
                 os.path.join(_PROJECT, name, "blackUSB.map"))
            )

        nm = os.environ.get("WCET_NM", "arm-none-eabi-nm")
        for elf_path, map_path in builds:
            if elf_path and os.path.isfile(elf_path):
                symbol = NmSymbolResolver(elf_path, nm_path=nm).resolve("Appli_state")
                if symbol is not None:
                    return symbol.address
            addr = self._address_from_map(map_path, "Appli_state")
            if addr is not None:
                return addr

        self.fail(
            "could not resolve Appli_state address. Set WCET_APPLI_STATE_ADDR "
            "(0x200000f4 for the Release build, 0x200004cc for Debug), or point "
            "WCET_USB_HOST_ELF at the .elf with arm-none-eabi-nm on PATH."
        )

    @staticmethod
    def _address_from_map(map_path: str, symbol: str) -> int | None:
        """Pull a symbol's address out of a GNU ld .map file.

        The map lists each symbol as e.g. '  0x00000000200000f4  Appli_state'.
        """
        if not map_path or not os.path.isfile(map_path):
            return None
        try:
            with open(map_path, "r", errors="replace") as handle:
                for line in handle:
                    parts = line.split()
                    if (
                        len(parts) == 2
                        and parts[1] == symbol
                        and parts[0].startswith("0x")
                    ):
                        return int(parts[0], 16)
        except OSError:
            return None
        return None

    def _appli_state(self) -> int:
        return self.target.read_u8(self.appli_state_addr)

    # -- the test ------------------------------------------------------------

    def test_enumerate_reaches_application_ready(self) -> None:
        abi = Stm32OtgFsHostAbi(self.target, base_address=self.base, layout=self.layout)
        device = UsbHostCdcDevice(abi)

        # Attach the simulated device and bring the port up.
        device.attach(speed=SPEED_FULL, timeout_s=self.timeout_s)

        # Enumeration has not run yet, so the CDC class cannot be active.
        self.assertNotEqual(
            self._appli_state(), APPLICATION_READY,
            "host reported APPLICATION_READY before enumeration even started",
        )

        # Service the whole enumeration; finish when the firmware signals the CDC
        # class is active (APPLICATION_READY).
        result = device.enumerate(
            done=lambda: self._appli_state() == APPLICATION_READY,
            timeout_s=float(os.environ.get("WCET_USB_HOST_ENUM_TIMEOUT", "20.0")),
        )

        final = self._appli_state()
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
        bulk_in = abi.channel_info(4)
        self.assertEqual(bulk_in.endpoint, 0x81, "channel 4 should be the CDC bulk IN endpoint")


if __name__ == "__main__":
    unittest.main()
