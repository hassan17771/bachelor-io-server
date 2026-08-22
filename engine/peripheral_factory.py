"""Resolve each mock's `class:` to a driver class and an ABI implementation."""
from __future__ import annotations

import fnmatch
import importlib
from typing import Dict, List, Optional

from engine.config_parser import ConfigError
from engine.config_schema import MockConfig, SocConfig
from peripherals.can.backends.stm32_bxcan import Stm32BxcanAbi
from peripherals.usb.host.backends.stm32_otgfs import Stm32OtgFsHostAbi
from target.gateway import TargetGateway

# `class:` -> driver class. SoC-independent: a class driver is reusable
# across SoCs by design, so this mapping never depends on `soc.part`.
_DRIVERS: Dict[str, str] = {
    "cdc": "peripherals.usb.host.cdc.UsbHostCdcDevice",
    "can": "peripherals.can.bus.CanBus",
}

# `soc.part` glob -> bxCAN Rx0 NVIC IRQn. Unlike USB's single OTG_FS_IRQn,
# bxCAN's IRQ line depends on the part, so this stays a table instead of a
# default. A second board is one more row.
_CAN_IRQN_BY_PART: Dict[str, int] = {
    "STM32F103*": 20,  # USB_LP_CAN1_RX0_IRQn
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
    `Stm32OtgFsHostAbi`; `can` further needs an `irqn` resolvable from
    `_CAN_IRQN_BY_PART`; everything else is a `ConfigError`.
    """
    if peripheral_class == "cdc" and soc.part.startswith("STM32"):
        return Stm32OtgFsHostAbi(gateway, base_address=mmb_base)
    if peripheral_class == "can":
        irqn = _resolve_can_irqn(soc.part)
        if irqn is not None:
            return Stm32BxcanAbi(gateway, base_address=mmb_base, irqn=irqn)
    raise ConfigError(
        f"no ABI available for peripheral class '{peripheral_class}' "
        f"on SoC part '{soc.part}'"
    )


def _resolve_can_irqn(part: str) -> Optional[int]:
    for pattern, irqn in _CAN_IRQN_BY_PART.items():
        if fnmatch.fnmatch(part, pattern):
            return irqn
    return None
