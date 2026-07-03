from __future__ import annotations

import os
import unittest

from backends.jlink import JLinkBackend, JLinkConnectionConfig


def _env_int(name: str, default: int | None = None) -> int:
    value = os.environ.get(name)
    if value is None:
        if default is None:
            raise unittest.SkipTest(f"{name} is required for this hardware test")
        return default
    return int(value, 0)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class JLinkBackendMemoryTest(unittest.TestCase):
    """Hardware smoke test for JLinkBackend.

    This test expects a JLinkGDBServerCLExe instance to already be connected to
    the target. It writes a short pattern to RAM, reads it back, and restores the
    original bytes before exiting.

    Example server:

        JLinkGDBServerCLExe -device STM32F103C8 -if SWD -speed 1000 -port 2331

    Example test run:

        WCET_JLINK_TEST_ADDR=0x20004A38 python3 -m unittest \
            tests.hardware.test_jlink_memory

    Choose an address in writable RAM that the running firmware will not modify.
    """

    def test_write_read_restore_ram_block(self) -> None:
        host = os.environ.get("WCET_JLINK_HOST", "127.0.0.1")
        port = _env_int("WCET_JLINK_PORT", 2331)
        timeout_s = float(os.environ.get("WCET_JLINK_TIMEOUT", "2.0"))
        address = _env_int("WCET_JLINK_TEST_ADDR")
        size = _env_int("WCET_JLINK_TEST_SIZE", 16)
        halt_target = _env_bool("WCET_JLINK_TEST_HALT", False)

        if size <= 0:
            raise ValueError("WCET_JLINK_TEST_SIZE must be positive")

        pattern = bytes(((0xA5 + i * 17) & 0xFF) for i in range(size))
        config = JLinkConnectionConfig(host=host, gdb_port=port, timeout_s=timeout_s)

        with JLinkBackend(config) as target:
            if halt_target:
                target.halt()

            original = target.read(address, size)
            try:
                target.write(address, pattern)
                actual = target.read(address, size)
                self.assertEqual(actual, pattern)
            finally:
                target.write(address, original)
                if halt_target:
                    target.resume()


if __name__ == "__main__":
    unittest.main()
