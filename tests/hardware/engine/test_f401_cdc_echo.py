"""The CDC echo suite against a real F401 over the J-Link GDB server.

The only test that proves the whole chain end to end: the ELF lookup found the
block on the build that is actually flashed, the config reached the board, and
the application really does echo. Every tier below this one runs against a
stand-in and can only prove the wiring.

The board is described by tests/hardware/fixtures/f401_jlink.yaml; which build
is flashed on it is a runtime input, so WCET_FIRMWARE_ELF is required.

Example server:
    JLinkGDBServerCLExe -device STM32F401RC -if SWD -speed 4000 -port 2331

Example test run:
    WCET_USB_HOST_TEST=1 \
    WCET_FIRMWARE_ELF=../f401-USBsim/Release/f401_USBsim.elf \
        python3 -m unittest tests.hardware.engine.test_f401_cdc_echo
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engine.test_engine import run_suite
from tests.support.hardware import env_truthy

SUITE = Path(__file__).parents[2] / "robot" / "cdc_echo.robot"
CONFIG = Path(__file__).parents[1] / "fixtures" / "f401_jlink.yaml"


class CdcEchoHardwareTest(unittest.TestCase):
    def setUp(self) -> None:
        if not env_truthy("WCET_USB_HOST_TEST"):
            raise unittest.SkipTest(
                "set WCET_USB_HOST_TEST=1 to run the engine hardware tests"
            )
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.outdir = Path(tmpdir.name)

    def test_the_target_echoes_what_the_mock_sends(self) -> None:
        result = run_suite(SUITE, CONFIG, outdir=self.outdir)

        # Robot has already printed the failing keyword and its message.
        self.assertTrue(result.passed, "the echo suite failed on hardware")
        self.assertEqual(result.pass_count, 1)


if __name__ == "__main__":
    unittest.main()
