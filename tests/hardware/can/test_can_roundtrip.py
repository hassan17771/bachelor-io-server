"""CAN command/status round trip against the real target over SWD/J-Link.

This is a HARDWARE test, and the architecture's acceptance criterion: it
proves unmodified f103-cansim ROLE_DEVICE firmware, talking to the IO server
standing in for ROLE_TESTER, entirely through the CanSimBlock MMB -- one
Blue Pill, no second board, no transceiver, no breadboard bus.

ROLE_DEVICE (Core/Src/main.c) filters for CAN_ID_COMMAND and, on receiving
one, applies it to the LED and echoes it straight back as CAN_ID_STATUS. This
test injects a CAN_ID_COMMAND frame via CanBus.send_frame and asserts
CAN_ID_STATUS comes back carrying the same value.

    # start the J-Link GDB server first, e.g.
    #   JLinkGDBServerCLExe -device STM32F103C8 -if SWD -speed 4000 -port 2331
    cd wcet-io-server
    WCET_CAN_TEST=1 WCET_FIRMWARE_ELF=tests/hardware/can/fixture/f103_CANsim.elf \
        python -m pytest tests/hardware/can/test_can_roundtrip.py -v

See tests/hardware/can/fixture/f103_can_jlink.yaml for the board description and
tests/support/hardware.py for the shared WCET_JLINK_HOST/PORT/TIMEOUT env vars.
"""
from __future__ import annotations

import time
import unittest
from pathlib import Path

from engine.context import build_context
from peripherals.can.abi import CanSimError
from tests.support.hardware import env_float, env_truthy

CONFIG = Path(__file__).parent / "fixture" / "f103_can_jlink.yaml"

# f103-cansim's Core/Inc/main.h: ROLE_DEVICE listens for CAN_ID_COMMAND and
# echoes the applied value back as CAN_ID_STATUS.
CAN_ID_COMMAND = 0x100
CAN_ID_STATUS = 0x101

STACK_TIMEOUT_S = 2.0
STACK_POLL_S = 0.05


class CanRoundtripHardwareTest(unittest.TestCase):
    def setUp(self) -> None:
        if not env_truthy("WCET_CAN_TEST"):
            raise unittest.SkipTest("set WCET_CAN_TEST=1 to run the CAN hardware test")

        self.context = build_context(CONFIG)
        self.context.gateway.connect()
        self.addCleanup(self.context.gateway.close)
        self.bus = self.context.peripherals["can_bus"]

        # Fresh boot for isolation, same as the USB hardware tests.
        self.context.gateway.reset(halt_after=False)
        self._await_can_stack()

    def _await_can_stack(self) -> None:
        """Poll validate() until HAL_CAN_Start_Sim has published the magic."""
        deadline = time.monotonic() + STACK_TIMEOUT_S
        while True:
            try:
                self.bus.abi.validate()
                return
            except CanSimError as exc:
                if time.monotonic() >= deadline:
                    self.fail(
                        f"CAN sim stack never came up ({exc}). Is the firmware "
                        "built with the Simulation configuration and flashed?"
                    )
                time.sleep(STACK_POLL_S)

    def test_command_echoes_back_as_status(self) -> None:
        value = 0x2A
        self.bus.send_frame(CAN_ID_COMMAND, bytes([value]))

        frame = self.bus.receive_frame(timeout_s=env_float("WCET_CAN_TIMEOUT", 3.0))

        self.assertEqual(frame.id, CAN_ID_STATUS)
        self.assertEqual(frame.data, bytes([value]))


if __name__ == "__main__":
    unittest.main()
