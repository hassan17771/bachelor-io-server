"""In-memory dataclasses for the SoC + Mock List config."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Union


@dataclass(frozen=True)
class ServerProbeConfig:
    """Probe reached through an already-running GDB server / OpenOCD daemon.

    Unset fields fall through to the backend's own connection defaults,
    which differ per backend (J-Link GDB 2331, OpenOCD telnet 4444).
    """

    host: Optional[str] = None
    port: Optional[int] = None
    timeout_s: Optional[float] = None


@dataclass(frozen=True)
class DirectProbeConfig:
    """Probe driven over USB by the backend itself. Not implemented yet."""

    serial: Optional[str] = None
    timeout_s: Optional[float] = None


ProbeConfig = Union[ServerProbeConfig, DirectProbeConfig]


@dataclass(frozen=True)
class TimeoutConfig:
    default_s: float


@dataclass(frozen=True)
class SocConfig:
    part: str
    backend: str
    probe: Optional[ProbeConfig] = None
    timeouts: Optional[TimeoutConfig] = None


@dataclass(frozen=True)
class MockConfig:
    # `peripheral_class` comes from the YAML `class:` key (`class` is reserved).
    # `device` is untyped passthrough identity data (e.g. CDC vid/pid).
    name: str
    peripheral_class: str
    channels: Optional[int] = None
    device: Optional[Mapping[str, Any]] = None
