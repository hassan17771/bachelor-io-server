"""Select and construct the target gateway named by a config's `soc.backend`."""
from __future__ import annotations

import os
from dataclasses import replace
from typing import Callable, Dict, Optional

from backends import (
    JLinkBackend,
    JLinkConnectionConfig,
    OpenOcdBackend,
    OpenOcdConnectionConfig,
)
from engine.config_parser import ConfigError
from engine.config_schema import DirectProbeConfig, ProbeConfig, SocConfig
from target.gateway import TargetGateway

FAKE_BACKEND = "fake"
ALLOW_FAKE_ENV = "WCET_ALLOW_FAKE_BACKEND"


def _build_jlink(soc: SocConfig) -> TargetGateway:
    probe = _server_probe(soc.probe)
    config = JLinkConnectionConfig()
    if probe is not None:
        config = replace(config, **_endpoint(probe, port_field="gdb_port"))
    return JLinkBackend(config)


def _build_openocd(soc: SocConfig) -> TargetGateway:
    probe = _server_probe(soc.probe)
    config = OpenOcdConnectionConfig()
    if probe is not None:
        config = replace(config, **_endpoint(probe, port_field="telnet_port"))
    return OpenOcdBackend(config)


def _build_fake(soc: SocConfig) -> TargetGateway:
    # Imported here so that engine/ carries no import-time dependency on the
    # test tree: the module loads fine wherever tests/ is not shipped.
    from tests.support.fake_gateway import FakeMmbGateway

    return FakeMmbGateway()


_BACKENDS: Dict[str, Callable[[SocConfig], TargetGateway]] = {
    "jlink": _build_jlink,
    "openocd": _build_openocd,
    FAKE_BACKEND: _build_fake,
}


def build_backend(soc: SocConfig) -> TargetGateway:
    """Construct (but do not connect) the gateway for `soc.backend`."""
    if soc.backend == FAKE_BACKEND and os.environ.get(ALLOW_FAKE_ENV) != "1":
        raise ConfigError(
            f"backend '{FAKE_BACKEND}' talks to no target at all, so a suite "
            f"running on it proves nothing about hardware; set "
            f"{ALLOW_FAKE_ENV}=1 to say you meant it"
        )
    try:
        factory = _BACKENDS[soc.backend]
    except KeyError:
        raise NotImplementedError(
            f"backend '{soc.backend}' is not implemented; "
            f"available backends: {', '.join(sorted(_BACKENDS))}"
        ) from None
    return factory(soc)


def _server_probe(probe: Optional[ProbeConfig]) -> Optional[ProbeConfig]:
    if isinstance(probe, DirectProbeConfig):
        raise NotImplementedError(
            "probe connection 'direct' is not implemented; both backends "
            "currently attach to an already-running debug server, so use "
            "connection 'server'"
        )
    return probe


def _endpoint(probe: ProbeConfig, port_field: str) -> Dict[str, object]:
    """Probe fields that were actually set, keyed for the backend's config.

    Unset fields are omitted so the backend's own defaults survive.
    """
    fields = {"host": probe.host, port_field: probe.port, "timeout_s": probe.timeout_s}
    return {k: v for k, v in fields.items() if v is not None}
