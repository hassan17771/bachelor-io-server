from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from typing import Optional

from target.capabilities import BackendCapabilities
from target.gateway import BaseTargetGateway, TargetConnectionError, TargetError
from target.memory import chunk_bytes
from target.symbols import SymbolResolver


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JLinkConnectionConfig:
    host: str = "127.0.0.1"
    gdb_port: int = 2331
    timeout_s: float = 2.0
    max_read_chunk: int = 1024
    max_write_chunk: int = 512


class GdbRemoteClient:
    def __init__(self, config: JLinkConnectionConfig) -> None:
        self.config = config
        self.sock: Optional[socket.socket] = None

    def connect(self) -> None:
        try:
            self.sock = socket.create_connection(
                (self.config.host, self.config.gdb_port),
                timeout=self.config.timeout_s,
            )
        except OSError as exc:
            raise TargetConnectionError(
                f"failed to connect to J-Link GDB server at "
                f"{self.config.host}:{self.config.gdb_port}"
            ) from exc
        self.sock.settimeout(self.config.timeout_s)

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def read_memory(self, address: int, size: int) -> bytes:
        response = self.request(f"m{address:x},{size:x}")
        self._raise_on_error(response)
        try:
            data = bytes.fromhex(response)
        except ValueError as exc:
            raise TargetError(f"invalid GDB memory read response: {response!r}") from exc
        if len(data) != size:
            raise TargetError(f"J-Link returned {len(data)} bytes, expected {size}")
        return data

    def write_memory(self, address: int, data: bytes) -> None:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "J-Link memory write: address=0x%08X size=%d data=%s",
                address,
                len(data),
                data.hex(" "),
            )
        response = self.request(f"M{address:x},{len(data):x}:{data.hex()}")
        self._expect_ok(response, "write memory")

    def monitor_command(self, command: str) -> str:
        encoded = command.encode("utf-8").hex()
        self._send_packet(f"qRcmd,{encoded}")
        console = []
        while True:
            response = self._read_packet()
            if response.startswith("O"):
                try:
                    console.append(bytes.fromhex(response[1:]).decode("utf-8", errors="replace"))
                except ValueError:
                    console.append(response[1:])
                continue
            self._expect_ok(response, f"monitor {command!r}")
            return "".join(console)

    def interrupt(self) -> str:
        sock = self._require_socket()
        sock.sendall(b"\x03")
        return self._read_packet()

    def request(self, payload: str) -> str:
        self._send_packet(payload)
        return self._read_packet()

    def send_no_response(self, payload: str) -> None:
        self._send_packet(payload)

    def _send_packet(self, payload: str) -> None:
        sock = self._require_socket()
        raw = payload.encode("ascii")
        packet = b"$" + raw + b"#" + f"{sum(raw) & 0xFF:02x}".encode("ascii")
        sock.sendall(packet)
        ack = sock.recv(1)
        if ack == b"+":
            return
        if ack == b"-":
            raise TargetError(f"GDB remote rejected packet {payload!r}")
        if ack == b"":
            raise TargetConnectionError("GDB remote connection closed while waiting for ACK")
        raise TargetError(f"unexpected GDB remote acknowledgement: {ack!r}")

    def _read_packet(self) -> str:
        sock = self._require_socket()
        while True:
            ch = sock.recv(1)
            if ch == b"":
                raise TargetConnectionError("GDB remote connection closed while reading packet")
            if ch == b"$":
                break
            if ch in (b"+", b"-"):
                continue

        payload = bytearray()
        while True:
            ch = sock.recv(1)
            if ch == b"":
                raise TargetConnectionError("GDB remote connection closed inside packet")
            if ch == b"#":
                break
            payload.extend(ch)

        checksum = sock.recv(2)
        if len(checksum) != 2:
            raise TargetError("truncated GDB remote checksum")
        try:
            expected = int(checksum.decode("ascii"), 16)
        except ValueError as exc:
            raise TargetError(f"invalid GDB remote checksum: {checksum!r}") from exc
        actual = sum(payload) & 0xFF
        if expected != actual:
            sock.sendall(b"-")
            raise TargetError("GDB remote packet checksum mismatch")
        sock.sendall(b"+")
        return payload.decode("ascii")

    @staticmethod
    def _raise_on_error(response: str) -> None:
        if response.startswith("E"):
            raise TargetError(f"GDB remote error: {response}")

    def _expect_ok(self, response: str, operation: str) -> None:
        self._raise_on_error(response)
        if response != "OK":
            raise TargetError(f"unexpected response for {operation}: {response!r}")

    def _require_socket(self) -> socket.socket:
        if self.sock is None:
            raise TargetConnectionError("J-Link GDB remote client is not connected")
        return self.sock


class JLinkBackend(BaseTargetGateway):
    def __init__(
        self,
        config: Optional[JLinkConnectionConfig] = None,
        symbol_resolver: Optional[SymbolResolver] = None,
    ) -> None:
        self.config = config or JLinkConnectionConfig()
        super().__init__(
            BackendCapabilities(
                name="jlink-gdb",
                can_resolve_symbols=symbol_resolver is not None,
                max_read_chunk=self.config.max_read_chunk,
                max_write_chunk=self.config.max_write_chunk,
                notes=("Uses the GDB remote protocol exposed by JLinkGDBServer.",),
            ),
            symbol_resolver=symbol_resolver,
        )
        self.client = GdbRemoteClient(self.config)

    def connect(self) -> "JLinkBackend":
        self.client.connect()
        return self

    def close(self) -> None:
        self.client.close()

    def read(self, address: int, size: int) -> bytes:
        if size < 0:
            raise ValueError("size must be non-negative")
        if size == 0:
            return b""
        chunks = []
        remaining = size
        cursor = address
        while remaining:
            n = min(remaining, self.config.max_read_chunk)
            chunks.append(self.client.read_memory(cursor, n))
            cursor += n
            remaining -= n
        return b"".join(chunks)

    def _write(self, address: int, data: bytes) -> None:
        for block in chunk_bytes(address, data, self.config.max_write_chunk):
            self.client.write_memory(block.address, block.data)

    def halt(self) -> None:
        try:
            self.client.monitor_command("halt")
        except TargetError:
            self.client.interrupt()

    def resume(self) -> None:
        try:
            self.client.monitor_command("go")
        except TargetError:
            self.client.send_no_response("c")

    def reset(self, halt_after: bool = False) -> None:
        command = "reset halt" if halt_after else "reset"
        self.client.monitor_command(command)

    def monitor_command(self, command: str) -> str:
        return self.client.monitor_command(command)
