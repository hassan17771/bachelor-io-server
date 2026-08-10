"""Resolve each mock's `class:` to a driver class and an ABI implementation."""
from __future__ import annotations

import importlib
from typing import Dict, List

from engine.config_parser import ConfigError
from engine.config_schema import MockConfig, SocConfig
from peripherals.usb.host.backends.stm32_otgfs import Stm32OtgFsHostAbi
from target.gateway import TargetGateway

# `class:` -> driver class. SoC-independent: a class driver is reusable
# across SoCs by design, so this mapping never depends on `soc.part`.
_DRIVERS: Dict[str, str] = {
    "cdc": "peripherals.usb.host.cdc.UsbHostCdcDevice",
}


def build_peripherals(
    mocks: List[MockConfig], soc: SocConfig, gateway: TargetGateway, mmb_base: int
) -> Dict[str, object]:
    """Construct each mock's ABI and driver, keyed by its `name:`."""
    return {mock.name: _build_one(mock, soc, gateway, mmb_base) for mock in mocks}


def _build_one(
    mock: MockConfig, soc: SocConfig, gateway: TargetGateway, mmb_base: int
) -> object:
    driver_class = _resolve_driver(mock.peripheral_class)
    abi = _resolve_abi(soc, mock.peripheral_class, gateway, mmb_base)
    return driver_class(abi)


def _resolve_driver(peripheral_class: str) -> type:
    try:
        path = _DRIVERS[peripheral_class]
    except KeyError:
        raise ConfigError(f"unknown peripheral class '{peripheral_class}'") from None
    module_path, class_name = path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _resolve_abi(
    soc: SocConfig, peripheral_class: str, gateway: TargetGateway, mmb_base: int
) -> object:
    """`soc.part` + `peripheral_class` -> ABI. A hardcoded stub, not a
    capability model: every STM32 part is assumed to support `cdc` via
    `Stm32OtgFsHostAbi`; everything else is a `ConfigError`.
    """
    if peripheral_class == "cdc" and soc.part.startswith("STM32"):
        return Stm32OtgFsHostAbi(gateway, base_address=mmb_base)
    raise ConfigError(
        f"no ABI available for peripheral class '{peripheral_class}' "
        f"on SoC part '{soc.part}'"
    )
