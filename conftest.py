"""Pytest bootstrap for the wcet-io-server repo.

Puts the repo root on sys.path once, centrally, so test files can import
`backends`, `firmware_abi`, `peripherals`, `target`, and the `tests` package
without each file doing its own sys.path.insert dance.
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
