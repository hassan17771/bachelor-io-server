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
from firmware_abi.can.stm32_bxcan import CAN_SIM_SYMBOL
from firmware_abi.usb.host.stm32_otgfs import USBH_SIM_SYMBOL
from target.gateway import TargetGateway
from target.symbols import ElfSymbolResolver

FIRMWARE_ELF_ENV = "WCET_FIRMWARE_ELF"

# `class:` -> MMB symbol. Defaults to "cdc" so the hardware-diagnostic
# scripts that call resolve_mmb_base() with no class argument keep resolving
# usbh_sim unchanged.
_MMB_SYMBOL_BY_CLASS: Dict[str, str] = {
    "cdc": USBH_SIM_SYMBOL,
    "can": CAN_SIM_SYMBOL,
}


@dataclass(frozen=True)
class TestContext:
    __test__ = False  # not a pytest test class despite the name

    soc: SocConfig
    gateway: TargetGateway
    peripherals: Dict[str, object]


def resolve_mmb_base(
    elf: Optional[Union[str, Path]] = None, peripheral_class: str = "cdc"
) -> int:
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
    try:
        symbol_name = _MMB_SYMBOL_BY_CLASS[peripheral_class]
    except KeyError:
        raise ConfigError(
            f"unknown peripheral class '{peripheral_class}'"
        ) from None
    symbol = ElfSymbolResolver(path).resolve(symbol_name)
    if symbol is None:
        raise ConfigError(
            f"no '{symbol_name}' symbol in {path}; "
            "is this a simulation-enabled build?"
        )
    return symbol.address


def build_context(
    path: Union[str, Path], elf: Optional[Union[str, Path]] = None
) -> TestContext:
    """Parse `path` and construct its gateway and peripherals, unconnected."""
    config = parse_config(path)
    mmb_base = resolve_mmb_base(elf, config.mocks[0].peripheral_class)
    gateway = build_backend(config.soc)
    peripherals = build_peripherals(config.mocks, config.soc, gateway, mmb_base)
    return TestContext(soc=config.soc, gateway=gateway, peripherals=peripherals)
