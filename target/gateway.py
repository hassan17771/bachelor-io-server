from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Callable, Optional, TypeVar

from .capabilities import BackendCapabilities
from .memory import coerce_bytes, pack_uint, unpack_uint
from .symbols import SymbolResolver


T = TypeVar("T")

NVIC_ISPR_BASE = 0xE000E200


class TargetError(RuntimeError):
    pass


class TargetConnectionError(TargetError):
    pass


class TargetTimeoutError(TargetError):
    pass


class SymbolNotFoundError(TargetError):
    pass


class TargetGateway(ABC):
    @property
    @abstractmethod
    def capabilities(self) -> BackendCapabilities:
        raise NotImplementedError

    @abstractmethod
    def read(self, address: int, size: int) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def write(self, address: int, data: bytes) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_u8(self, address: int) -> int:
        raise NotImplementedError

    @abstractmethod
    def write_u8(self, address: int, value: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_u16(self, address: int) -> int:
        raise NotImplementedError

    @abstractmethod
    def write_u16(self, address: int, value: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_u32(self, address: int) -> int:
        raise NotImplementedError

    @abstractmethod
    def write_u32(self, address: int, value: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def resolve_symbol(self, name: str) -> int:
        raise NotImplementedError

    @abstractmethod
    def pend_irq(self, irqn: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def halt(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def resume(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def reset(self, halt_after: bool = False) -> None:
        raise NotImplementedError

    @abstractmethod
    def transaction(self, fn: Callable[["TargetGateway"], T]) -> T:
        raise NotImplementedError

    @abstractmethod
    def wait_until(
        self,
        predicate: Callable[[], T | None | bool],
        timeout_s: float,
        interval_s: float = 0.01,
        description: str = "condition",
    ) -> T:
        raise NotImplementedError


class BaseTargetGateway(TargetGateway):
    def __init__(
        self,
        capabilities: BackendCapabilities,
        symbol_resolver: Optional[SymbolResolver] = None,
    ) -> None:
        self._capabilities = capabilities
        self.symbol_resolver = symbol_resolver

    @property
    def capabilities(self) -> BackendCapabilities:
        return self._capabilities

    def connect(self) -> "BaseTargetGateway":
        return self

    def close(self) -> None:
        return None

    def __enter__(self) -> "BaseTargetGateway":
        return self.connect()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def read_u8(self, address: int) -> int:
        return unpack_uint(self.read(address, 1))

    def write_u8(self, address: int, value: int) -> None:
        self.write(address, pack_uint(value, 8))

    def read_u16(self, address: int) -> int:
        return unpack_uint(self.read(address, 2))

    def write_u16(self, address: int, value: int) -> None:
        self.write(address, pack_uint(value, 16))

    def read_u32(self, address: int) -> int:
        return unpack_uint(self.read(address, 4))

    def write_u32(self, address: int, value: int) -> None:
        self.write(address, pack_uint(value, 32))

    def write(self, address: int, data: bytes) -> None:
        self._write(address, coerce_bytes(data))

    @abstractmethod
    def _write(self, address: int, data: bytes) -> None:
        raise NotImplementedError

    def resolve_symbol(self, name: str) -> int:
        if self.symbol_resolver is None:
            raise SymbolNotFoundError(f"no symbol resolver configured for {name!r}")
        symbol = self.symbol_resolver.resolve(name)
        if symbol is None:
            raise SymbolNotFoundError(f"symbol not found: {name}")
        return symbol.address

    def pend_irq(self, irqn: int) -> None:
        if irqn < 0:
            raise ValueError("IRQ number must be non-negative")
        ispr = NVIC_ISPR_BASE + (irqn // 32) * 4
        bit = 1 << (irqn % 32)
        self.write_u32(ispr, bit)

    def halt(self) -> None:
        raise TargetError(f"{self.capabilities.name} backend does not implement halt")

    def resume(self) -> None:
        raise TargetError(f"{self.capabilities.name} backend does not implement resume")

    def reset(self, halt_after: bool = False) -> None:
        raise TargetError(f"{self.capabilities.name} backend does not implement reset")

    def transaction(self, fn: Callable[[TargetGateway], T]) -> T:
        return fn(self)

    def wait_until(
        self,
        predicate: Callable[[], T | None | bool],
        timeout_s: float,
        interval_s: float = 0.01,
        description: str = "condition",
    ) -> T:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result  # type: ignore[return-value]
            time.sleep(interval_s)
        raise TargetTimeoutError(f"timed out waiting for {description}")
