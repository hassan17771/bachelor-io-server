from __future__ import annotations

import struct
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple


@dataclass(frozen=True)
class Symbol:
    name: str
    address: int
    source: str = "unknown"


class SymbolResolver(ABC):
    @abstractmethod
    def resolve(self, name: str) -> Optional[Symbol]:
        raise NotImplementedError


class StaticSymbolResolver(SymbolResolver):
    def __init__(self, symbols: Dict[str, int | Symbol]) -> None:
        self._symbols = symbols

    def resolve(self, name: str) -> Optional[Symbol]:
        value = self._symbols.get(name)
        if value is None:
            return None
        if isinstance(value, Symbol):
            return value
        return Symbol(name=name, address=int(value), source="static")


class NmSymbolResolver(SymbolResolver):
    def __init__(self, elf_path: str | Path, nm_path: str = "arm-none-eabi-nm") -> None:
        self.elf_path = Path(elf_path)
        self.nm_path = nm_path
        self._cache: Optional[Dict[str, Symbol]] = None

    def resolve(self, name: str) -> Optional[Symbol]:
        return self._symbols().get(name)

    def _symbols(self) -> Dict[str, Symbol]:
        if self._cache is None:
            self._cache = self._load_symbols()
        return self._cache

    def _load_symbols(self) -> Dict[str, Symbol]:
        try:
            proc = subprocess.run(
                [self.nm_path, "-n", str(self.elf_path)],
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception:
            return {}

        symbols: Dict[str, Symbol] = {}
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            try:
                address = int(parts[0], 16)
            except ValueError:
                continue
            symbols[parts[2]] = Symbol(
                name=parts[2],
                address=address,
                source=f"nm:{self.elf_path}",
            )
        return symbols


# e_ident: magic, ELFCLASS32, ELFDATA2LSB. Cortex-M is little-endian 32-bit,
# so anything else is not a firmware image this project can be looking at.
_ELF32_LE_IDENT = b"\x7fELF\x01\x01"
_EH_SIZE = 52   # sizeof(Elf32_Ehdr)
_SYM_SIZE = 16  # sizeof(Elf32_Sym)
_SHT_SYMTAB = 2


class ElfSymbolResolver(SymbolResolver):
    """Read addresses out of an ELF32's ``.symtab`` with no toolchain at all.

    The firmware build is the only input, so a host without a cross binutils
    can still locate a target symbol. Anything that is not a readable
    little-endian ELF32 -- a missing file, a 64-bit object, a linker map --
    yields no symbols rather than an exception, matching
    :class:`NmSymbolResolver`.
    """

    def __init__(self, elf_path: str | Path) -> None:
        self.elf_path = Path(elf_path)
        self._cache: Optional[Dict[str, Symbol]] = None

    def resolve(self, name: str) -> Optional[Symbol]:
        return self._symbols().get(name)

    def _symbols(self) -> Dict[str, Symbol]:
        if self._cache is None:
            self._cache = self._load_symbols()
        return self._cache

    def _load_symbols(self) -> Dict[str, Symbol]:
        try:
            data = self.elf_path.read_bytes()
        except OSError:
            return {}
        try:
            return self._parse(data)
        except (struct.error, IndexError):
            return {}

    def _parse(self, data: bytes) -> Dict[str, Symbol]:
        if len(data) < _EH_SIZE or not data.startswith(_ELF32_LE_IDENT):
            return {}

        (sh_off,) = struct.unpack_from("<I", data, 32)
        sh_entsize, sh_num = struct.unpack_from("<HH", data, 46)
        headers = [
            struct.unpack_from("<10I", data, sh_off + i * sh_entsize)
            for i in range(sh_num)
        ]

        symbols: Dict[str, Symbol] = {}
        for header in headers:
            _, sh_type, _, _, sh_offset, sh_size, sh_link = header[:7]
            if sh_type != _SHT_SYMTAB:
                continue
            strtab = headers[sh_link]
            symbols.update(self._read_symbols(data, (sh_offset, sh_size), strtab[4:6]))
        return symbols

    def _read_symbols(
        self, data: bytes, symtab: Tuple[int, int], strtab: Tuple[int, int]
    ) -> Dict[str, Symbol]:
        offset, size = symtab
        str_offset, str_size = strtab
        names = data[str_offset : str_offset + str_size]
        source = f"elf:{self.elf_path}"

        symbols: Dict[str, Symbol] = {}
        for i in range(size // _SYM_SIZE):
            st_name, st_value = struct.unpack_from("<II", data, offset + i * _SYM_SIZE)
            if st_name == 0:  # the reserved null symbol, and unnamed entries
                continue
            name = _string_at(names, st_name)
            if name:
                symbols[name] = Symbol(name=name, address=st_value, source=source)
        return symbols


def _string_at(table: bytes, offset: int) -> str:
    end = table.find(b"\0", offset)
    raw = table[offset:] if end < 0 else table[offset:end]
    return raw.decode("utf-8", "replace")


class CompositeSymbolResolver(SymbolResolver):
    def __init__(self, resolvers: Iterable[SymbolResolver]) -> None:
        self.resolvers = tuple(resolvers)

    def resolve(self, name: str) -> Optional[Symbol]:
        for resolver in self.resolvers:
            symbol = resolver.resolve(name)
            if symbol is not None:
                return symbol
        return None


def resolve_symbol_address(resolver: Optional[SymbolResolver], name: str) -> Optional[int]:
    if resolver is None:
        return None
    symbol = resolver.resolve(name)
    return None if symbol is None else symbol.address
