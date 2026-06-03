from __future__ import annotations

import os
import unittest

from backends.jlink import JLinkBackend, JLinkConnectionConfig
from firmware_abi.usb2 import DEFAULT_USB_SIM_BASE, Usb2SimConfig, parse_hex_bytes
from peripherals.usb2 import Usb2CdcMockHost, Usb2Simulator
from target.symbols import NmSymbolResolver

import logging

logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)s:%(name)s:%(message)s",
)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)), 0)


class Usb2CdcJLinkTest(unittest.TestCase):
    """CDC integration smoke test through the USB simulation layer.

    This test expects:

    - Firmware flashed and built with USB_SIMULATION enabled.
    - JLinkGDBServerCLExe already connected to the STM32 target.
    - The target firmware running, or WCET_USB_CDC_RESUME_TARGET=1.

    Example server:

        JLinkGDBServerCLExe -device STM32F103C8 -if SWD -speed 1000 -port 2331

    Example test run:

        WCET_USB_CDC_TEST=1 \
        WCET_USB_SIM_BASE=0x20004C00 \
        WCET_USB_CDC_PAYLOAD_TEXT=test \
        python3 -m unittest tests.test_usb2_cdc_jlink -v
    """

    def test_enumerate_write_and_print_response(self) -> None:
        if not _env_bool("WCET_USB_CDC_TEST"):
            raise unittest.SkipTest("set WCET_USB_CDC_TEST=1 to run this hardware test")

        verbose = _env_bool("WCET_USB_CDC_VERBOSE", True)
        jlink_config = JLinkConnectionConfig(
            host=os.environ.get("WCET_JLINK_HOST", "127.0.0.1"),
            gdb_port=_env_int("WCET_JLINK_PORT", 2331),
            timeout_s=float(os.environ.get("WCET_JLINK_TIMEOUT", "2.0")),
        )

        elf_path = os.environ.get("WCET_USB_SIM_ELF")
        symbol_resolver = NmSymbolResolver(elf_path) if elf_path else None

        usb_config = Usb2SimConfig(
            base_address=_env_int("WCET_USB_SIM_BASE", DEFAULT_USB_SIM_BASE),
            resolve_symbol=_env_bool("WCET_USB_SIM_RESOLVE_SYMBOL", bool(elf_path)),
        )

        payload_hex = os.environ.get("WCET_USB_CDC_PAYLOAD_HEX")
        if payload_hex:
            payload = parse_hex_bytes(payload_hex)
        else:
            payload = os.environ.get("WCET_USB_CDC_PAYLOAD_TEXT", "test").encode("utf-8")

        address = _env_int("WCET_USB_CDC_ADDRESS", 1)
        max_response = _env_int("WCET_USB_CDC_MAX_RESPONSE", 64)
        settle_time_s = float(os.environ.get("WCET_USB_CDC_SETTLE_TIME", "0.05"))
        tx_timeout_s = float(os.environ.get("WCET_USB_CDC_TX_TIMEOUT", "1.0"))

        if verbose:
            print(
                f"[jlink] connecting to {jlink_config.host}:{jlink_config.gdb_port} "
                f"(timeout={jlink_config.timeout_s}s)",
                flush=True,
            )
        with JLinkBackend(jlink_config, symbol_resolver=symbol_resolver) as target:
            if verbose:
                print("[jlink] connected", flush=True)
            if _env_bool("WCET_USB_CDC_RESUME_TARGET", False):
                if verbose:
                    print("[jlink] resuming target", flush=True)
                target.resume()
                if verbose:
                    print("[jlink] target resume requested", flush=True)

            usb = Usb2Simulator(target, usb_config)
            cdc = Usb2CdcMockHost(
                usb,
                settle_time_s=settle_time_s,
                tx_timeout_s=tx_timeout_s,
                verbose=verbose,
            )

            enumeration = cdc.enumerate(address=address)
            print(enumeration.summary())

            response = cdc.cdc_write(payload, max_response=max_response)
            response_text = response.decode("utf-8", errors="replace")
            print(f"CDC response hex: {response.hex()}")
            print(f"CDC response text: {response_text}")

        expected_hex = os.environ.get("WCET_USB_CDC_EXPECT_HEX")
        expected_text = os.environ.get("WCET_USB_CDC_EXPECT_TEXT")
        if expected_hex is not None:
            self.assertEqual(response, parse_hex_bytes(expected_hex))
        elif expected_text is not None:
            self.assertEqual(response_text, expected_text)
        else:
            self.assertIsInstance(response, bytes)


if __name__ == "__main__":
    unittest.main()
