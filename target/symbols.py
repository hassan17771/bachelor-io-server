from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional


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
