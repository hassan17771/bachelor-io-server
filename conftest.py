"""Pytest bootstrap for the wcet-io-server repo.

Puts the repo root on sys.path once, centrally, so test files can import
`backends`, `firmware_abi`, `peripherals`, and `target` without each file
doing its own sys.path.insert dance. (tests/usb-host has a hyphen in its
name and is not an importable package, so pytest's rootdir handling alone
does not cover it.)
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
