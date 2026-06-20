"""CDC bulk ping/pong against the real target over SWD/J-Link.

This is a HARDWARE test. After enumeration the target firmware (usb_host.c)
becomes the talker: while the CDC class is active it transmits "ping <n>" on the
CDC bulk-OUT pipe and receives the reply on the bulk-IN pipe, logging both over
USART1:

    CDC write: ping 1
    CDC read: pong 1

This test plays the device side. It enumerates the mock, then services N
ping/pong rounds with UsbHostCdcDevice.wait_and_echo, answering each "ping <n>"
with "pong <n>". The firmware only emits ping <n+1> from its receive-complete
callback -- i.e. after it has consumed pong <n> into its RX buffer -- so seeing
the ping counter advance 1, 2, 3, ... is proof that every reply reached the
target's RX buffer. Nothing is mocked.

    # start the J-Link GDB server first, e.g.
    #   JLinkGDBServerCLExe -device STM32F411CE -if SWD -speed 4000 -port 2331
    cd wcet-io-server
    python tests/usb-host/test_usb_host_echo_4_2.py

Watch the firmware's own view of the exchange on the USART1 console (PA15 TX).
"""
from __future__ import annotations

import os
import sys
import unittest

# 'tests/usb-host' has a hyphen, so it is not an importable package. Put the
# wcet-io-server root (three levels up) on sys.path to run this file directly.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_PROJECT = os.path.dirname(_ROOT)  # repo root holding Debug/ and Release/

from backends.jlink import JLinkBackend, JLinkConnectionConfig
from firmware_abi.usbh_otgfs import (
    DEFAULT_USBH_SIM_LAYOUT,
    OTG_FS_IRQn,
    USBH_SIM_MAGIC,
    resolve_usbh_sim_base,
)
from peripherals.usb_host import SPEED_FULL, UsbHostCdcDevice
from peripherals.usb_host_otgfs import Stm32OtgFsHostAbi
from target.gateway import TargetError
from target.symbols import NmSymbolResolver

NVIC_ISER_BASE = 0xE000E100

# usb_host.c: APPLICATION_READY is enum value 2 (set on HOST_USER_CLASS_ACTIVE).
APPLICATION_READY = 2
APPLI_STATE_NAMES = {0: "IDLE", 1: "START", 2: "READY", 3: "DISCONNECT"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return default if value is None else int(value, 0)


class UsbHostCdcEchoTest(unittest.TestCase):
    """Run the ping/pong exchange end-to-end on the target.

    Requires a JLinkGDBServerCLExe already attached to the STM32 and firmware
    flashed with the sim shims plus the CDC application code.
    """

    def setUp(self) -> None:
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
        """Pull a symbol's address out of a GNU ld .map file."""
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

    @staticmethod
    def _ping_number(payload: bytes) -> int:
        """Parse the <n> out of a b"ping <n>" payload."""
        parts = payload.split()
        return int(parts[1]) if len(parts) >= 2 else -1

    # -- the test ------------------------------------------------------------

    def test_cdc_ping_pong_round_trips(self) -> None:
        abi = Stm32OtgFsHostAbi(self.target, base_address=self.base, layout=self.layout)
        device = UsbHostCdcDevice(abi)

        # Bring the port up and enumerate to APPLICATION_READY.
        device.attach(speed=SPEED_FULL, timeout_s=self.timeout_s)
        device.enumerate(
            done=lambda: self._appli_state() == APPLICATION_READY,
            timeout_s=float(os.environ.get("WCET_USB_HOST_ENUM_TIMEOUT", "20.0")),
        )
        self.assertEqual(
            self._appli_state(), APPLICATION_READY,
            "enumeration did not reach APPLICATION_READY; cannot start the CDC exchange",
        )

        rounds = int(os.environ.get("WCET_CDC_ROUNDS", "5"))

        def responder(received: bytes) -> bytes:
            # Answer "ping <n>" with "pong <n>".
            parts = received.split()
            number = parts[1] if len(parts) >= 2 else b"?"
            return b"pong " + number

        seen_numbers = []
        for expected_n in range(1, rounds + 1):
            echo = device.wait_and_echo(responder, timeout_s=10.0)
            print(f"  MCU->dev {echo.received!r:<16}  dev->MCU {echo.sent!r}")

            self.assertTrue(
                echo.received.startswith(b"ping "),
                f"expected a CDC 'ping', got {echo.received!r}",
            )
            number = self._ping_number(echo.received)
            self.assertEqual(
                number, expected_n,
                f"ping counter out of order: got {number}, expected {expected_n} "
                f"({echo.received!r})",
            )
            self.assertEqual(echo.sent, b"pong " + str(expected_n).encode())
            seen_numbers.append(number)

        # Each ping <n+1> is only emitted from the firmware's receive callback,
        # after it consumed pong <n> -- so a clean 1..rounds sequence is proof the
        # replies reached the target's RX buffer.
        self.assertEqual(
            seen_numbers, list(range(1, rounds + 1)),
            f"firmware did not advance the ping counter cleanly: {seen_numbers}",
        )


if __name__ == "__main__":
    unittest.main()
