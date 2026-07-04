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
    WCET_USB_HOST_TEST=1 python -m pytest tests/hardware/usb/host/test_cdc_echo.py

Watch the firmware's own view of the exchange on the USART1 console (PA15 TX).
See tests/support/hardware.py for the environment-variable table.
"""
from __future__ import annotations

import unittest

from peripherals.usb.common.requests import SPEED_FULL
from peripherals.usb.host.cdc import UsbHostCdcDevice
from tests.support.hardware import (
    APPLICATION_READY,
    UsbHostHardwareTest,
    env_float,
    env_int,
)


class UsbHostCdcEchoTest(UsbHostHardwareTest):
    RESOLVE_APPLI_STATE = True

    @staticmethod
    def _ping_number(payload: bytes) -> int:
        """Parse the <n> out of a b"ping <n>" payload."""
        parts = payload.split()
        return int(parts[1]) if len(parts) >= 2 else -1

    def test_cdc_ping_pong_round_trips(self) -> None:
        device = UsbHostCdcDevice(self.abi)

        # Bring the port up and enumerate to APPLICATION_READY.
        device.attach(speed=SPEED_FULL, timeout_s=self.timeout_s)
        device.enumerate(
            done=lambda: self.appli_state() == APPLICATION_READY,
            timeout_s=env_float("WCET_USB_HOST_ENUM_TIMEOUT", 20.0),
        )
        self.assertEqual(
            self.appli_state(), APPLICATION_READY,
            "enumeration did not reach APPLICATION_READY; cannot start the CDC exchange",
        )

        rounds = env_int("WCET_CDC_ROUNDS", 5)

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
