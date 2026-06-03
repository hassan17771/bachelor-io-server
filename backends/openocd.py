from __future__ import annotations

import logging
import re
import socket
from dataclasses import dataclass
from typing import List, Optional

from target.capabilities import BackendCapabilities
from target.gateway import BaseTargetGateway, TargetConnectionError, TargetError
from target.symbols import SymbolResolver


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenOcdConnectionConfig:
    host: str = "127.0.0.1"
    telnet_port: int = 4444
    timeout_s: float = 2.0


class OpenOcdTelnetClient:
    def __init__(self, config: OpenOcdConnectionConfig) -> None:
        self.config = config
        self.sock: Optional[socket.socket] = None

    def connect(self) -> None:
        try:
            self.sock = socket.create_connection(
                (self.config.host, self.config.telnet_port),
                timeout=self.config.timeout_s,
            )
        except OSError as exc:
            raise TargetConnectionError(
                f"failed to connect to OpenOCD telnet at "
                f"{self.config.host}:{self.config.telnet_port}"
            ) from exc
        self.sock.settimeout(self.config.timeout_s)
        self._drain_banner()

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def command(self, line: str) -> str:
        sock = self._require_socket()
        payload = (line.strip() + "\n").encode("ascii")
        sock.sendall(payload)
        chunks: List[bytes] = []
        while True:
            data = sock.recv(4096)
            if not data:
                break
            chunks.append(data)
            text = b"".join(chunks).decode("utf-8", errors="ignore")
            if re.search(r"\r?\n>\s*$", text):
                return re.sub(r"\r?\n>\s*$", "", text)
        return b"".join(chunks).decode("utf-8", errors="ignore")

    def _drain_banner(self) -> None:
        sock = self._require_socket()
        try:
            sock.recv(4096)
        except OSError:
            pass

    def _require_socket(self) -> socket.socket:
        if self.sock is None:
            raise TargetConnectionError("OpenOCD telnet client is not connected")
        return self.sock


class OpenOcdBackend(BaseTargetGateway):
    def __init__(
        self,
        config: Optional[OpenOcdConnectionConfig] = None,
        symbol_resolver: Optional[SymbolResolver] = None,
    ) -> None:
        super().__init__(
            BackendCapabilities(
                name="openocd",
                can_resolve_symbols=symbol_resolver is not None,
            ),
            symbol_resolver=symbol_resolver,
        )
        self.config = config or OpenOcdConnectionConfig()
        self.client = OpenOcdTelnetClient(self.config)

    def connect(self) -> "OpenOcdBackend":
        self.client.connect()
        return self

    def close(self) -> None:
        self.client.close()

    def read(self, address: int, size: int) -> bytes:
        if size < 0:
            raise ValueError("size must be non-negative")
        if size == 0:
            return b""
        out = self.client.command(f"mdb 0x{address:08X} {size}")
        values = _parse_dump_values(out, 2, size)
        if len(values) < size:
            raise TargetError(f"OpenOCD returned {len(values)} bytes, expected {size}")
        return bytes(values[:size])

    def _write(self, address: int, data: bytes) -> None:
        for offset, value in enumerate(data):
            write_address = address + offset
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "OpenOCD memory write: address=0x%08X size=1 data=%02X",
                    write_address,
                    value,
                )
            self.client.command(f"mwb 0x{write_address:08X} 0x{value:02X}")

    def read_u16(self, address: int) -> int:
        out = self.client.command(f"mdh 0x{address:08X} 1")
        values = _parse_dump_values(out, 4, 1)
        if not values:
            raise TargetError(f"failed to read 16-bit value at 0x{address:08X}")
        return values[0]

    def write_u16(self, address: int, value: int) -> None:
        value &= 0xFFFF
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "OpenOCD memory write: address=0x%08X size=2 data=%04X",
                address,
                value,
            )
        self.client.command(f"mwh 0x{address:08X} 0x{value:04X}")

    def read_u32(self, address: int) -> int:
        out = self.client.command(f"mdw 0x{address:08X} 1")
        values = _parse_dump_values(out, 8, 1)
        if not values:
            raise TargetError(f"failed to read 32-bit value at 0x{address:08X}")
        return values[0]

    def write_u32(self, address: int, value: int) -> None:
        value &= 0xFFFFFFFF
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "OpenOCD memory write: address=0x%08X size=4 data=%08X",
                address,
                value,
            )
        self.client.command(f"mww 0x{address:08X} 0x{value:08X}")

    def halt(self) -> None:
        self.client.command("halt")

    def resume(self) -> None:
        self.client.command("resume")

    def reset(self, halt_after: bool = False) -> None:
        self.client.command("reset halt" if halt_after else "reset")

    def command(self, line: str) -> str:
        return self.client.command(line)


def _parse_dump_values(output: str, hex_digits: int, limit: int) -> List[int]:
    values: List[int] = []
    token_re = re.compile(rf"\b(?:0x)?([0-9A-Fa-f]{{{hex_digits}}})\b")
    for line in output.splitlines():
        if ":" in line:
            line = line.split(":", 1)[1]
        for match in token_re.finditer(line):
            values.append(int(match.group(1), 16))
            if len(values) >= limit:
                return values
    return values
