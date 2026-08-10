"""Process-wide handle on the live context, so keyword libraries can find it.

Robot Framework constructs keyword libraries with no arguments, so they cannot
be handed a peripheral -- they look one up here instead. One suite runs at a
time, so a single module-level slot is enough.
"""
from __future__ import annotations

from typing import Optional

from engine.context import TestContext
from target.gateway import TargetGateway

_context: Optional[TestContext] = None


def install(context: TestContext) -> None:
    global _context
    _context = context


def peripheral(name: str) -> object:
    ctx = _installed()
    try:
        return ctx.peripherals[name]
    except KeyError:
        known = ", ".join(sorted(ctx.peripherals)) or "none"
        raise LookupError(f"no peripheral named {name!r} is configured (have: {known})") from None


def gateway() -> TargetGateway:
    return _installed().gateway


def reset() -> None:
    global _context
    _context = None


def _installed() -> TestContext:
    if _context is None:
        raise LookupError("no test context is installed; the engine installs one per suite run")
    return _context
