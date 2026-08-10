"""Hardware smoke test for build_context against real firmware.

Requires the board flashed with the -DUSB_HOST_SIMULATION firmware build (see
tests/support/hardware.py) so the MMB magic is available to read through the
resolved ABI.

Example server:
    JLinkGDBServerCLExe -device STM32F411CE -if SWD -speed 4000 -port 2331

Example test run:
    WCET_USB_HOST_TEST=1 WCET_FIRMWARE_ELF=../Release/blackUSB.elf \
        python3 -m unittest tests.hardware.engine.test_context
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engine.context import build_context
from firmware_abi.usb.host.stm32_otgfs import USBH_SIM_MAGIC
from tests.support.hardware import env_truthy, hil_config_yaml

NVIC_ISER_BASE = 0xE000E100


class BuildContextHardwareTest(unittest.TestCase):
    def setUp(self) -> None:
        if not env_truthy("WCET_USB_HOST_TEST"):
            raise unittest.SkipTest("set WCET_USB_HOST_TEST=1 to run the engine hardware tests")
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        config_path = hil_config_yaml(Path(tmpdir.name))

        self.ctx = build_context(config_path)
        self.ctx.gateway.connect()
        self.addCleanup(self.ctx.gateway.close)

    def test_reads_the_mmb_magic_through_the_resolved_abi(self) -> None:
        abi = self.ctx.peripherals["usb_host"].abi

        # Sim Init skips HAL_HCD_MspInit, so firmware never enables OTG_FS_IRQn;
        # the IO server must, or USBH_LL_Start's magic write is never observed.
        iser = NVIC_ISER_BASE + (abi.irqn // 32) * 4
        self.ctx.gateway.write_u32(iser, 1 << (abi.irqn % 32))

        self.ctx.gateway.wait_until(
            lambda: self.ctx.gateway.read_u32(abi.base_address + abi.layout.magic)
            == USBH_SIM_MAGIC,
            timeout_s=3.0,
            description="USBH_LL_Start to publish the MMB magic",
        )


if __name__ == "__main__":
    unittest.main()
