"""Load a SoC + Mock List config YAML and validate it into dataclasses."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

import yaml

from engine.config_schema import (
    DirectProbeConfig,
    MockConfig,
    ProbeConfig,
    ServerProbeConfig,
    SocConfig,
    TimeoutConfig,
)

KNOWN_BACKENDS = frozenset({"jlink", "openocd", "fake"})
KNOWN_PERIPHERAL_CLASSES = frozenset({"cdc", "can"})
PROBE_CONNECTIONS = frozenset({"server", "direct"})


class ConfigError(ValueError):
    """Raised when a config YAML is structurally invalid."""


@dataclass(frozen=True)
class ResolvedConfig:
    soc: SocConfig
    mocks: list[MockConfig]


def parse_config(path: Union[str, Path]) -> ResolvedConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ConfigError("config is empty or not a YAML mapping")
    return ResolvedConfig(
        soc=_parse_soc(data.get("soc")),
        mocks=_parse_mocks(data.get("mocks")),
    )


def _parse_soc(raw: Any) -> SocConfig:
    if raw is None:
        raise ConfigError("config is missing the required 'soc:' section")
    if not isinstance(raw, dict):
        raise ConfigError("'soc:' must be a mapping")
    part = raw.get("part")
    if not part:
        raise ConfigError("'soc.part' is required")
    backend = raw.get("backend")
    if not backend:
        raise ConfigError("'soc.backend' is required")
    if backend not in KNOWN_BACKENDS:
        raise ConfigError(
            f"unknown backend '{backend}'; expected one of "
            f"{', '.join(sorted(KNOWN_BACKENDS))}"
        )
    # Rejected rather than ignored: a config still pinning an address would
    # otherwise look like it decides where the MMB is, and silently not.
    if "gateway" in raw:
        raise ConfigError(
            "'soc.gateway' is no longer supported; the MMB address is read "
            "from the firmware ELF, not from the config"
        )
    return SocConfig(
        part=part,
        backend=backend,
        probe=_parse_probe(raw.get("probe")),
        timeouts=_parse_timeouts(raw.get("timeouts")),
    )


def _parse_probe(raw: Any) -> Optional[ProbeConfig]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("'soc.probe' must be a mapping")
    connection = raw.get("connection", "server")
    if connection not in PROBE_CONNECTIONS:
        raise ConfigError(
            f"unknown probe connection '{connection}'; expected one of "
            f"{', '.join(sorted(PROBE_CONNECTIONS))}"
        )
    if connection == "server":
        _reject_foreign_fields(raw, {"serial"}, "server")
        return ServerProbeConfig(
            host=raw.get("host"),
            port=raw.get("port"),
            timeout_s=raw.get("timeout_s"),
        )
    _reject_foreign_fields(raw, {"host", "port"}, "direct")
    return DirectProbeConfig(
        serial=raw.get("serial"),
        timeout_s=raw.get("timeout_s"),
    )


def _reject_foreign_fields(raw: dict, foreign: set, connection: str) -> None:
    """Fail on probe fields that cannot affect the selected connection mode."""
    present = sorted(foreign & raw.keys())
    if present:
        raise ConfigError(
            f"'soc.probe.{present[0]}' has no meaning for "
            f"connection '{connection}'"
        )


def _parse_timeouts(raw: Any) -> Optional[TimeoutConfig]:
    if raw is None:
        return None
    if not isinstance(raw, dict) or "default_s" not in raw:
        raise ConfigError("'soc.timeouts' requires 'default_s'")
    return TimeoutConfig(default_s=raw["default_s"])


def _parse_mocks(raw: Any) -> list[MockConfig]:
    if not raw:
        raise ConfigError("config must define exactly one entry under 'mocks:'")
    if not isinstance(raw, list):
        raise ConfigError("'mocks:' must be a list")
    if len(raw) > 1:
        raise ConfigError(
            "multiple 'mocks:' entries are not supported yet; define exactly one"
        )
    return [_parse_mock(raw[0])]


def _parse_mock(raw: Any) -> MockConfig:
    if not isinstance(raw, dict):
        raise ConfigError("each 'mocks:' entry must be a mapping")
    name = raw.get("name")
    if not name:
        raise ConfigError("each mock requires a 'name'")
    peripheral_class = raw.get("class")
    if not peripheral_class:
        raise ConfigError("each mock requires a 'class'")
    if peripheral_class not in KNOWN_PERIPHERAL_CLASSES:
        raise ConfigError(
            f"unknown peripheral class '{peripheral_class}'; expected one of "
            f"{', '.join(sorted(KNOWN_PERIPHERAL_CLASSES))}"
        )
    return MockConfig(
        name=name,
        peripheral_class=peripheral_class,
        channels=raw.get("channels"),
        device=raw.get("device"),
    )
