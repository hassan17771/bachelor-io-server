from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass(frozen=True)
class BackendCapabilities:
    name: str
    can_halt: bool = True
    can_resume: bool = True
    can_reset: bool = True
    can_resolve_symbols: bool = False
    supports_transactions: bool = True
    max_read_chunk: Optional[int] = None
    max_write_chunk: Optional[int] = None
    notes: Tuple[str, ...] = field(default_factory=tuple)

    def with_note(self, note: str) -> "BackendCapabilities":
        return BackendCapabilities(
            name=self.name,
            can_halt=self.can_halt,
            can_resume=self.can_resume,
            can_reset=self.can_reset,
            can_resolve_symbols=self.can_resolve_symbols,
            supports_transactions=self.supports_transactions,
            max_read_chunk=self.max_read_chunk,
            max_write_chunk=self.max_write_chunk,
            notes=(*self.notes, note),
        )
