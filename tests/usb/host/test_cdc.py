"""UsbHostCdcDevice's own defaults, not covered by the class-agnostic core tests.

Runs with no J-Link and no target.
"""
from __future__ import annotations

import inspect

from peripherals.usb.host.cdc import UsbHostCdcDevice

# Measured against the F401/J-Link setup: a full enumeration (9-10 control
# transfers, each a few seconds of GDB round trips) took 25-28s, with
# individual transfers up to 4.4s. Extrapolating that worst per-transfer cost
# across the whole sequence puts the ceiling near 47s.
MEASURED_WORST_CASE_ENUMERATION_S = 47.0


def test_enumerate_default_timeout_clears_a_real_debug_probe():
    """`Attach Device` calls `enumerate()` with no override, so this default is
    the only thing standing between a slow debug link and a false
    EnumerationError -- see tests/hardware/engine/test_f401_cdc_echo.py.
    """
    default = inspect.signature(UsbHostCdcDevice.enumerate).parameters["timeout_s"].default
    assert default >= MEASURED_WORST_CASE_ENUMERATION_S
