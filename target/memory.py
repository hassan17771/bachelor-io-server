from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence, Tuple


@dataclass(frozen=True)
class AddressRange:
    start: int
    size: int

    @property
    def end(self) -> int:
        return self.start + self.size

    def contains(self, address: int, size: int = 1) -> bool:
        return self.start <= address and (address + size) <= self.end


@dataclass(frozen=True)
class MemoryBlock:
    address: int
    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def range(self) -> AddressRange:
        return AddressRange(self.address, self.size)


def pack_uint(value: int, width_bits: int, byteorder: str = "little") -> bytes:
    if width_bits % 8 != 0:
        raise ValueError("width_bits must be a multiple of 8")
    size = width_bits // 8
    mask = (1 << width_bits) - 1
    return int(value & mask).to_bytes(size, byteorder)


def unpack_uint(data: bytes, byteorder: str = "little") -> int:
    return int.from_bytes(data, byteorder)


def chunk_bytes(address: int, data: bytes, max_chunk: int) -> Iterator[MemoryBlock]:
    if max_chunk <= 0:
        raise ValueError("max_chunk must be positive")
    for offset in range(0, len(data), max_chunk):
        yield MemoryBlock(address + offset, data[offset : offset + max_chunk])


def coerce_bytes(data: bytes | bytearray | Sequence[int] | Iterable[int]) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, bytearray):
        return bytes(data)
    return bytes(data)
