"""Shared bring-up for the opt-in USB-host hardware tests.

Every hardware test needs the same scaffolding: skip unless
WCET_USB_HOST_TEST=1, connect to the J-Link GDB server, make sure the core is
running with OTG_FS enabled in the NVIC, and (usually) reset to a fresh boot
and wait for the firmware's host stack to publish the MMB magic.
:class:`UsbHostHardwareTest` does all of that once, driven by class flags, so
each test file states only what it exercises.

Environment variables understood here:

    WCET_USB_HOST_TEST     1 to run the hardware tests (skip otherwise)
    WCET_FIRMWARE_ELF      the .elf flashed on the board; the MMB address is
                           read from its symbol table (required)
    WCET_USB_HOST_TIMEOUT  generic wait timeout, seconds (per-test default)
    WCET_JLINK_HOST        GDB server host (default 127.0.0.1)
    WCET_JLINK_PORT        GDB server port (default 2331)
    WCET_JLINK_TIMEOUT     GDB socket timeout, seconds (default 2.0)
    WCET_APPLI_STATE_ADDR  address of the firmware global Appli_state
    WCET_USB_HOST_ELF      firmware .elf to resolve Appli_state from instead
    WCET_NM                nm binary (default arm-none-eabi-nm)
    WCET_STM32_PART        soc.part for the engine config YAML (default STM32F411CE)
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from typing import Callable, Optional

from backends.jlink import JLinkBackend, JLinkConnectionConfig
from engine.config_parser import ConfigError
from engine.context import resolve_mmb_base
from firmware_abi.usb.host.stm32_otgfs import (
    DEFAULT_USBH_SIM_LAYOUT,
    OTG_FS_IRQn,
    USBH_SIM_MAGIC,
)
from peripherals.usb.host.backends.stm32_otgfs import Stm32OtgFsHostAbi
from target.gateway import TargetError
from target.symbols import NmSymbolResolver
from tests.support.fake_gateway import MmbView

NVIC_ISER_BASE = 0xE000E100

# usb_host.c application states; APPLICATION_READY (2) is set when the CDC
# class goes active (HOST_USER_CLASS_ACTIVE).
APPLICATION_IDLE = 0
APPLICATION_START = 1
APPLICATION_READY = 2
APPLICATION_DISCONNECT = 3
APPLI_STATE_NAMES = {0: "IDLE", 1: "START", 2: "READY", 3: "DISCONNECT"}

# The wcet-io-server root; its parent is the project root holding the
# firmware's Debug/ and Release/ build outputs (for symbol resolution).
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_PROJECT = os.path.dirname(_ROOT)


def env_truthy(name: str) -> bool:
    value = os.environ.get(name)
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return default if value is None else int(value, 0)


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return default if value is None else float(value)


def jlink_config_from_env() -> JLinkConnectionConfig:
    return JLinkConnectionConfig(
        host=os.environ.get("WCET_JLINK_HOST", "127.0.0.1"),
        gdb_port=env_int("WCET_JLINK_PORT", 2331),
        timeout_s=env_float("WCET_JLINK_TIMEOUT", 2.0),
    )


def hil_config_yaml(tmp_path: Path, **overrides: str) -> Path:
    """Write an engine config YAML wired to the same board as the env vars above.

    Uses `backend: jlink` with `soc.part`/`soc.probe` taken from
    WCET_STM32_PART/WCET_JLINK_HOST/WCET_JLINK_PORT/WCET_JLINK_TIMEOUT, so the
    engine hardware tests share one board/GDB-server setup with the rest of
    this file. `overrides` replaces individual YAML values by key
    (`part`, `host`, `port`, `timeout_s`).
    """
    values = {
        "part": os.environ.get("WCET_STM32_PART", "STM32F411CE"),
        "host": os.environ.get("WCET_JLINK_HOST", "127.0.0.1"),
        "port": env_int("WCET_JLINK_PORT", 2331),
        "timeout_s": env_float("WCET_JLINK_TIMEOUT", 2.0),
    }
    values.update(overrides)
    text = (
        "soc:\n"
        f"  part: {values['part']}\n"
        "  backend: jlink\n"
        "  probe:\n"
        f"    host: {values['host']}\n"
        f"    port: {values['port']}\n"
        f"    timeout_s: {values['timeout_s']}\n"
        "mocks:\n"
        "  - name: usb_host\n"
        "    class: cdc\n"
    )
    path = tmp_path / "hil_config.yaml"
    path.write_text(text)
    return path


def address_from_map(map_path: str, symbol: str) -> Optional[int]:
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


def resolve_appli_state(fail: Callable[[str], None]) -> int:
    """Resolve the address of the firmware global ``Appli_state``.

    In order: WCET_APPLI_STATE_ADDR, then the ELF named by WCET_USB_HOST_ELF,
    then the project's Release/ and Debug/ builds -- each first via
    arm-none-eabi-nm, then via the sibling .map file (checked in, needs no
    toolchain). Calls ``fail`` (the TestCase's fail method) if nothing works.
    """
    raw = os.environ.get("WCET_APPLI_STATE_ADDR")
    if raw:
        return int(raw, 0)

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
        addr = address_from_map(map_path, "Appli_state")
        if addr is not None:
            return addr

    fail(
        "could not resolve Appli_state address. Set WCET_APPLI_STATE_ADDR "
        "(0x200000f4 for the Release build, 0x200004cc for Debug), or point "
        "WCET_USB_HOST_ELF at the .elf with arm-none-eabi-nm on PATH."
    )
    raise AssertionError("unreachable")  # fail() always raises


class UsbHostHardwareTest(unittest.TestCase):
    """Base for tests that drive the real target over the J-Link GDB server.

    setUp connects and leaves ready to use:

        self.target   the live TargetGateway (JLinkBackend)
        self.abi      Stm32OtgFsHostAbi over the target
        self.view     MmbView for arranging/inspecting raw MMB fields
        self.layout / self.base / self.timeout_s

    Class flags tune the bring-up:

        RESET_ON_SETUP       reset to a fresh boot for test isolation
        WAIT_FOR_MAGIC       wait for USBH_LL_Start to publish the MMB magic
        RESOLVE_APPLI_STATE  resolve the Appli_state address (self.appli_state())
        DEFAULT_TIMEOUT_S    default for WCET_USB_HOST_TIMEOUT
    """

    RESET_ON_SETUP = True
    WAIT_FOR_MAGIC = True
    RESOLVE_APPLI_STATE = False
    DEFAULT_TIMEOUT_S = 3.0

    def setUp(self) -> None:
        if not env_truthy("WCET_USB_HOST_TEST"):
            raise unittest.SkipTest("set WCET_USB_HOST_TEST=1 to run the USB host hardware test")

        self.layout = DEFAULT_USBH_SIM_LAYOUT
        try:
            self.base = resolve_mmb_base()
        except ConfigError as exc:
            self.fail(f"could not locate the MMB in the flashed build: {exc}")
        self.timeout_s = env_float("WCET_USB_HOST_TIMEOUT", self.DEFAULT_TIMEOUT_S)

        self.target = JLinkBackend(jlink_config_from_env()).connect()
        self.view = MmbView(self.target, self.base, self.layout)
        self.abi = Stm32OtgFsHostAbi(self.target, base_address=self.base, layout=self.layout)

        if self.RESET_ON_SETUP:
            # Fresh boot for isolation between test cases.
            self.target.reset(halt_after=False)
        self._ensure_running()
        # Sim Init skips HAL_HCD_MspInit, so firmware never enables OTG_FS_IRQn;
        # the IO server must, or pend_irq does nothing.
        self._enable_irq(OTG_FS_IRQn)

        if self.WAIT_FOR_MAGIC:
            self._wait_for_magic()
        if self.RESOLVE_APPLI_STATE:
            self.appli_state_addr = resolve_appli_state(self.fail)

    def tearDown(self) -> None:
        target = getattr(self, "target", None)
        if target is not None:
            target.close()

    def _ensure_running(self) -> None:
        # Best effort: "go" is harmless if the target is already free-running.
        try:
            self.target.monitor_command("go")
        except TargetError:
            pass

    def _enable_irq(self, irqn: int) -> None:
        iser = NVIC_ISER_BASE + (irqn // 32) * 4
        self.target.write_u32(iser, 1 << (irqn % 32))

    def _wait_for_magic(self) -> None:
        """Wait until USBH_LL_Init (memset) and USBH_LL_Start (magic) have run."""
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

    def appli_state(self) -> int:
        return self.target.read_u8(self.appli_state_addr)
