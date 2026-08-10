"""Build the Config Manager's single entry point: parse, wire, hand back."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Union

from engine.backend_factory import build_backend
from engine.config_parser import ConfigError, parse_config
from engine.config_schema import SocConfig
from engine.peripheral_factory import build_peripherals
from firmware_abi.usb.host.stm32_otgfs import USBH_SIM_SYMBOL
from target.gateway import TargetGateway
from target.symbols import ElfSymbolResolver

FIRMWARE_ELF_ENV = "WCET_FIRMWARE_ELF"


@dataclass(frozen=True)
class TestContext:
    __test__ = False  # not a pytest test class despite the name

    soc: SocConfig
    gateway: TargetGateway
    peripherals: Dict[str, object]


def resolve_mmb_base(elf: Optional[Union[str, Path]] = None) -> int:
    """Address of the MMB in a firmware build, read from its symbol table.

    Which build is flashed depends on the invocation, not on the board, so the
    ELF comes from `elf` or from the environment -- never from the config YAML,
    which describes hardware and stays machine-independent.
    """
    source = elf if elf is not None else os.environ.get(FIRMWARE_ELF_ENV)
    if not source:
        raise ConfigError(
            "no firmware ELF: pass elf= or set "
            f"{FIRMWARE_ELF_ENV} to the build under test"
        )
    path = Path(source)
    if not path.is_file():
        raise ConfigError(f"firmware ELF not found: {path}")
    symbol = ElfSymbolResolver(path).resolve(USBH_SIM_SYMBOL)
    if symbol is None:
        raise ConfigError(
            f"no '{USBH_SIM_SYMBOL}' symbol in {path}; "
            "is this a simulation-enabled build?"
        )
    return symbol.address


def build_context(
    path: Union[str, Path], elf: Optional[Union[str, Path]] = None
) -> TestContext:
    """Parse `path` and construct its gateway and peripherals, unconnected."""
    config = parse_config(path)
    mmb_base = resolve_mmb_base(elf)
    gateway = build_backend(config.soc)
    peripherals = build_peripherals(config.mocks, config.soc, gateway, mmb_base)
    return TestContext(soc=config.soc, gateway=gateway, peripherals=peripherals)
