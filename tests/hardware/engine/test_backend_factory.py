"""Hardware smoke test for build_backend against a real J-Link GDB server.

Example server:
    JLinkGDBServerCLExe -device STM32F411CE -if SWD -speed 4000 -port 2331

Example test run:
    WCET_USB_HOST_TEST=1 python3 -m unittest tests.hardware.engine.test_backend_factory
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engine.backend_factory import build_backend
from engine.config_parser import parse_config
from tests.support.hardware import env_truthy, hil_config_yaml

# SCB->CPUID: fixed on every Cortex-M core, readable regardless of firmware --
# proves the endpoint mapping, not any particular firmware build.
CPUID_ADDR = 0xE000ED00


class BuildBackendHardwareTest(unittest.TestCase):
    def setUp(self) -> None:
        if not env_truthy("WCET_USB_HOST_TEST"):
            raise unittest.SkipTest("set WCET_USB_HOST_TEST=1 to run the engine hardware tests")
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        config_path = hil_config_yaml(Path(tmpdir.name))
        self.soc = parse_config(config_path).soc

    def test_connect_read_close_then_reconnect(self) -> None:
        gateway = build_backend(self.soc).connect()
        try:
            gateway.read_u32(CPUID_ADDR)
        finally:
            gateway.close()

        # close() must release the GDB server's single client slot.
        gateway = build_backend(self.soc).connect()
        try:
            gateway.read_u32(CPUID_ADDR)
        finally:
            gateway.close()


if __name__ == "__main__":
    unittest.main()
