"""In-memory fake TargetGateway + MMB view for unit-testing the Python ABI.

The ABI methods are pure transport logic: they read and write MMB
fields in a particular order. That logic can be verified without a J-Link or a
target by backing the gateway with a plain dict and inspecting what it wrote --
which is what this helper provides. Hardware behaviour (the middleware actually
emitting a SETUP) is exercised separately by the live test_usbh_conf_*sim tests.

`tests/usb-host` has a hyphen so it is not an importable package; tests add this
directory to sys.path and `import fake_mmb_gateway`.
"""
from __future__ import annotations

import os
import sys
from typing import Callable, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from target.capabilities import BackendCapabilities
from target.gateway import BaseTargetGateway


class FakeMmbGateway(BaseTargetGateway):
    """A TargetGateway backed by a sparse byte map -- no probe, no target.

    * reads/writes hit ``self.mem`` (default 0 for never-written bytes), so any
      address works including NVIC registers,
    * every block write is appended to ``self.writes`` as ``(address, data)`` so
      a test can assert ordering (e.g. urb_state=DONE written last),
    * ``pend_irq`` is recorded in ``self.pended`` and, if ``on_pend_irq`` is set,
      invokes it so a test can model the target's IRQ reaction.
    """

    def __init__(self, on_pend_irq: Optional[Callable[["FakeMmbGateway", int], None]] = None) -> None:
        super().__init__(BackendCapabilities(name="fake-mmb"))
        self.mem: dict[int, int] = {}
        self.writes: list[tuple[int, bytes]] = []
        self.pended: list[int] = []
        self.on_pend_irq = on_pend_irq

    def read(self, address: int, size: int) -> bytes:
        return bytes(self.mem.get(address + i, 0) for i in range(size))

    def _write(self, address: int, data: bytes) -> None:
        self.writes.append((address, bytes(data)))
        for i, b in enumerate(data):
            self.mem[address + i] = b & 0xFF

    def pend_irq(self, irqn: int) -> None:
        self.pended.append(irqn)
        if self.on_pend_irq is not None:
            self.on_pend_irq(self, irqn)


class MmbView:
    """Name-based read/write of MMB header and channel fields, for arranging
    and inspecting test state against a layout + base."""

    def __init__(self, gateway: BaseTargetGateway, base: int, layout) -> None:
        self.g = gateway
        self.base = base
        self.layout = layout

    # absolute addresses (for write-ordering assertions)
    def addr(self, field: str) -> int:
        return self.base + getattr(self.layout, field)

    def ch_addr(self, n: int, field: str) -> int:
        return self.base + self.layout.ch(n, field)

    # header fields
    def get_u8(self, field: str) -> int:
        return self.g.read_u8(self.addr(field))

    def set_u8(self, field: str, value: int) -> None:
        self.g.write_u8(self.addr(field), value)

    def get_u16(self, field: str) -> int:
        return self.g.read_u16(self.addr(field))

    def set_u16(self, field: str, value: int) -> None:
        self.g.write_u16(self.addr(field), value)

    def set_u32(self, field: str, value: int) -> None:
        self.g.write_u32(self.addr(field), value)

    # channel fields
    def ch_get_u8(self, n: int, field: str) -> int:
        return self.g.read_u8(self.ch_addr(n, field))

    def ch_set_u8(self, n: int, field: str, value: int) -> None:
        self.g.write_u8(self.ch_addr(n, field), value)

    def ch_get_u16(self, n: int, field: str) -> int:
        return self.g.read_u16(self.ch_addr(n, field))

    def ch_set_u16(self, n: int, field: str, value: int) -> None:
        self.g.write_u16(self.ch_addr(n, field), value)

    def ch_read(self, n: int, field: str, size: int) -> bytes:
        return self.g.read(self.ch_addr(n, field), size)

    def ch_write(self, n: int, field: str, data: bytes) -> None:
        self.g.write(self.ch_addr(n, field), data)

    def open_pipe(self, n: int, ep_addr: int, ep_type: int, ep_dir: int) -> None:
        """Mimic USBH_LL_OpenPipe filling a channel's endpoint identity."""
        self.ch_set_u8(n, "ep_addr", ep_addr)
        self.ch_set_u8(n, "ep_type", ep_type)
        self.ch_set_u8(n, "ep_dir", ep_dir)
